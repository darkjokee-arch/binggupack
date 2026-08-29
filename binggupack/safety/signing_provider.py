# -*- coding: utf-8 -*-
"""binggupack.safety.signing_provider — 순수 stdlib Ed25519 + SigningProvider (PW-Core 1).

목적
  trusted approval EVENT(approvals.jsonl) 를 비대칭 서명(Ed25519)으로 봉인해, consume 측이
  **공개키만으로** 승인 진위를 검증할 수 있게 하는 서명 계층의 기반 모듈이다. 서명키(sk)는 모델
  tool surface 밖(keychain/별도 프로세스 vault)에 두는 것을 전제한다.

정직 라벨 (★ 절대 준수 — 이 3문장은 attack_demo/RFC/SECURITY/PR 에도 동일 명시)
  1) L2(keychain 서명)는 owner 머신(Shell/FS MCP 병재)에서 위조를 못 막는다 — 같은 uid 셸이 keychain
     에서 sk 를 로드해 스스로 서명하면 "같은 머신 키 = 보안 연극"이 성립한다.
  2) config 의 kind 는 모델-writable 평문이라, kind:local_owner 한 줄로 서명 검증을 통째 skip 시킬 수
     있다(R-B6 · P1-B2 · out-of-scope). 즉 로컬 config 자체가 신뢰 경계 밖이다.
  3) L2 의 실질 값은 hosted/locked 배포(모델이 셸/keychain/ config 를 못 건드리는 환경)에서만 나온다.
  → 따라서 이 모듈/문서 어디에도 "로컬에서 위조 차단" · "로컬 보안 강화" 라고 쓰지 않는다. 로컬은
     '정직한 감사 흔적'이지 하드 통제가 아니다.

런타임 의존 0
  Ed25519 는 RFC 8032 를 hashlib.sha512 + 정수연산만으로 구현한다. cryptography/PyNaCl/keyring 등
  런타임 의존을 import 하지 않는다(top-level import 는 stdlib 뿐).

secret 위생 (영구금지 18)
  sk/키 원문을 print/log/repr 하지 않는다. describe_secret() 가 sha256 hash8 + 길이만 반환한다.

CLI: python -m binggupack.safety.signing_provider --selftest   ->  GATE=GO | GATE=BLOCK
     (네트워크 0 · 운영 ~/.binggupack 미접촉 · in-memory fake keychain 만)
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import secrets
import sys

# ══════════════════════════════════════════════════════════════════════════════════
# 1) 순수 stdlib Ed25519 (RFC 8032) — keygen / sign / verify
#    twisted Edwards -x^2 + y^2 = 1 + d x^2 y^2 (mod p), extended homogeneous 좌표.
#    verify 는 RFC 8032 cofactorless 검증식([S]B == R + [k]A), 비-canonical S(S>=L) reject,
#    malleability/전부-0/잘못된 point reject.
# ══════════════════════════════════════════════════════════════════════════════════
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512(b: bytes) -> bytes:
    return hashlib.sha512(b).digest()


def _modp_inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int):
    """압축 y + 부호비트 → x. 실패(비-잔여/범위초과) 시 None(decompression 실패)."""
    if y >= _P:
        return None
    x2 = (y * y - 1) * _modp_inv(_D * y * y + 1) % _P
    if x2 == 0:
        if sign:
            return None
        return 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


# base point B (y = 4/5, x 는 짝수 부호)
_BY = 4 * _modp_inv(5) % _P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % _P)
_NEUTRAL = (0, 1, 1, 0)


def _point_add(pt, qt):
    x1, y1, z1, t1 = pt
    x2, y2, z2, t2 = qt
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(s: int, pt):
    q = _NEUTRAL
    while s > 0:
        if s & 1:
            q = _point_add(q, pt)
        pt = _point_add(pt, pt)
        s >>= 1
    return q


def _point_equal(pt, qt):
    if (pt[0] * qt[2] - qt[0] * pt[2]) % _P != 0:
        return False
    if (pt[1] * qt[2] - qt[1] * pt[2]) % _P != 0:
        return False
    return True


def _point_compress(pt) -> bytes:
    zinv = _modp_inv(pt[2])
    x = pt[0] * zinv % _P
    y = pt[1] * zinv % _P
    return int(y | ((x & 1) << 255)).to_bytes(32, "little")


def _point_decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _secret_expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("ed25519 secret key must be 32 bytes")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8        # 하위 3비트 clear
    a |= (1 << 254)            # bit 254 set (bit 255 clear)
    return a, h[32:]


def ed25519_publickey(secret: bytes) -> bytes:
    """32바이트 sk → 32바이트 압축 공개키."""
    a, _prefix = _secret_expand(secret)
    return _point_compress(_point_mul(a, _B))


def ed25519_sign(secret: bytes, msg: bytes) -> bytes:
    """RFC 8032 결정적 서명. 반환 = R(32) || S(32) = 64바이트."""
    a, prefix = _secret_expand(secret)
    a_pub = _point_compress(_point_mul(a, _B))
    r = int.from_bytes(_sha512(prefix + msg), "little") % _L
    r_enc = _point_compress(_point_mul(r, _B))
    k = int.from_bytes(_sha512(r_enc + a_pub + msg), "little") % _L
    s = (r + k * a) % _L
    return r_enc + int(s).to_bytes(32, "little")


def ed25519_verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    """RFC 8032 cofactorless 검증. 조용히 True 내는 fail-open 을 피하려 모든 실패는 False.

    reject: 길이오류 · 잘못된 pk/decompression · 잘못된 R · 비-canonical S(S>=L) ·
            malleability/전부-0 · 검증식 불일치.
    """
    if not isinstance(public, (bytes, bytearray)) or len(public) != 32:
        return False
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    a = _point_decompress(bytes(public))
    if a is None:
        return False
    r_enc = bytes(signature[:32])
    r = _point_decompress(r_enc)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:                      # 비-canonical S / malleability reject
        return False
    k = int.from_bytes(_sha512(r_enc + bytes(public) + msg), "little") % _L
    sb = _point_mul(s, _B)
    ha = _point_mul(k, a)
    return _point_equal(sb, _point_add(r, ha))


# ══════════════════════════════════════════════════════════════════════════════════
# 2) canonical signing bytes — 서명 대상 완전성 + unknown-key reject
# ══════════════════════════════════════════════════════════════════════════════════
# 서명 record 의 바인딩 필드(sig 제외 전부). float 금지: approved_at/expires_at 는 int.
_BINDING_FIELDS = frozenset({
    "request_id", "protocol_version", "operation", "payload_digest", "ledger_id",
    "approval_nonce", "approved_at", "expires_at", "approver_channel", "record_type",
})
_SIG_KEY = "sig"


class _UnknownFieldError(ValueError):
    """서명 record 에 미서명 여분키(unknown-key) 포함 → binding_reject:unknown_key."""


class _IncompleteBindingError(ValueError):
    """서명 record 에 바인딩 필드 누락 → binding_reject:incomplete."""


def _canonical_signing_bytes(record: dict) -> bytes:
    """record(sig 제외) → 결정적 canonical bytes.

    sort_keys=True · ensure_ascii=False · separators=(",",":") · float repr 의존 0(정수만).
    바인딩 필드 전부(request_id/protocol_version/operation/payload_digest/ledger_id/approval_nonce/
    approved_at/expires_at/approver_channel/record_type) 포함 · sig 키 제외 · unknown-key reject.
    """
    if not isinstance(record, dict):
        raise _IncompleteBindingError("signed record must be a dict")
    keys = set(record.keys()) - {_SIG_KEY}
    unknown = keys - _BINDING_FIELDS
    if unknown:
        raise _UnknownFieldError("unknown signed keys: %s" % sorted(unknown))
    missing = _BINDING_FIELDS - keys
    if missing:
        raise _IncompleteBindingError("missing binding fields: %s" % sorted(missing))
    payload = {k: record[k] for k in _BINDING_FIELDS}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════════
# 3) secret 위생
# ══════════════════════════════════════════════════════════════════════════════════
def describe_secret(secret) -> dict:
    """sk 평문 미노출 요약 — sha256 hash8 + 길이만(영구금지 18). 본문은 반환 어디에도 없다."""
    if isinstance(secret, (bytes, bytearray)):
        raw = bytes(secret)
    elif isinstance(secret, str):
        raw = secret.encode("utf-8")
    else:
        raise ValueError("describe_secret: bytes|str 만")
    if not raw:
        raise ValueError("describe_secret: 비어있지 않은 secret")
    return {"sha256_hash8": hashlib.sha256(raw).hexdigest()[:8], "length": len(raw)}


# ══════════════════════════════════════════════════════════════════════════════════
# 4) keychain backend seam (lazy · import-time 부작용 0)
# ══════════════════════════════════════════════════════════════════════════════════
class KeychainBackend:
    """서명키 저장/로드 백엔드 계약. 실제 OS keychain 접근은 반드시 함수 내부 lazy import 로만."""

    kind = "abstract"

    def available(self) -> bool:  # pragma: no cover - 인터페이스
        raise NotImplementedError

    def load_or_create_signing_key(self, key_id: str):  # pragma: no cover - 인터페이스
        """(sk_bytes32, pk_bytes32) 반환. sk 는 절대 로깅/반환-노출 금지."""
        raise NotImplementedError


class InMemoryKeychainBackend(KeychainBackend):
    """테스트/attack_demo 기본 fake — 실 OS keychain · 실 ~/.binggupack 미접촉.

    seed 지정 시 결정적(KAT 재현). backend 미주입 상태에서 실 keychain 을 건드리면 FAIL 하도록,
    실 백엔드(default_keychain_backend)는 Core 1 에서 available=False(fail-closed)로만 존재한다.
    """

    kind = "in_memory_fake"

    def __init__(self, seed: "str | None" = None):
        self._store: "dict[str, tuple]" = {}
        self._seed = seed

    def available(self) -> bool:
        return True

    def load_or_create_signing_key(self, key_id: str):
        if key_id not in self._store:
            if self._seed is not None:
                sk = hashlib.sha256(("%s:%s" % (self._seed, key_id)).encode("utf-8")).digest()
            else:
                sk = secrets.token_bytes(32)
            pk = ed25519_publickey(sk)
            self._store[key_id] = (sk, pk)
        return self._store[key_id]


class _UnavailableKeychainBackend(KeychainBackend):
    """실 OS keychain 백엔드 placeholder — Core 1 미구현. 항상 available=False(headless/미구성 L0)."""

    kind = "unavailable"

    def available(self) -> bool:
        return False

    def load_or_create_signing_key(self, key_id: str):  # pragma: no cover - 도달 불가
        raise SigningUnavailable("keychain backend not available (Core 1 stub)")


def default_keychain_backend() -> KeychainBackend:
    """실 keychain 백엔드 로드(lazy). ctypes/subprocess/D-Bus 등은 반드시 아래 함수 내부에서만.

    Core 1 은 실 OS keychain 구현을 포함하지 않는다(별도 core). 미가용 백엔드를 반환해
    signing_provider_for 가 _UnavailableSigningProvider(fail-closed)로 귀결되게 한다. import-time
    부작용 0 — 이 함수를 호출하지 않으면 어떤 OS 서비스도 probe 하지 않는다.
    """
    try:
        # 후속 core 에서 실 백엔드 모듈이 생기면 여기서만 lazy import(top-level 금지).
        _kb = importlib.import_module("binggupack.safety.keychain_backend")
    except Exception:
        return _UnavailableKeychainBackend()
    try:
        return _kb.get_backend()
    except Exception:
        return _UnavailableKeychainBackend()


# ══════════════════════════════════════════════════════════════════════════════════
# 5) SigningProvider 추상 + KeychainProvider + _UnavailableSigningProvider
# ══════════════════════════════════════════════════════════════════════════════════
SIGNING_KINDS = frozenset({"keychain"})   # sig 부여/검증이 실제로 붙는 kind 집합


class SigningUnavailable(RuntimeError):
    """서명 provider 가 구성됐으나 backend 미가용(fail-closed) — sign/mint 시 raise."""


class SigningProvider:
    """서명 provider 추상. kind/public_key/sign_record/verify_signed/mint_signed."""

    kind: "str | None" = None
    ttl_seconds = 0
    pending_cap = 0

    @property
    def public_key(self):  # pragma: no cover - 인터페이스
        raise NotImplementedError

    def sign_record(self, record: dict) -> dict:  # pragma: no cover - 인터페이스
        raise NotImplementedError

    def verify_signed(self, record: dict) -> dict:  # pragma: no cover - 인터페이스
        raise NotImplementedError

    def mint_signed(self, request: dict, ttl_seconds: int, now, channel: str) -> dict:  # pragma: no cover
        raise NotImplementedError


def _verify_signed_with_pk(public_key, record: dict) -> dict:
    """공개키 + record → {ok[, reason]}. 검증 실패는 항상 명시 reason(fail-open 0)."""
    if public_key is None:
        return {"ok": False, "reason": "signing_unavailable"}
    if not isinstance(record, dict):
        return {"ok": False, "reason": "signed_record_invalid"}
    sig_hex = record.get(_SIG_KEY)
    if not isinstance(sig_hex, str) or not sig_hex:
        return {"ok": False, "reason": "signature_missing"}
    try:
        sig = bytes.fromhex(sig_hex)
    except ValueError:
        return {"ok": False, "reason": "signature_malformed"}
    if len(sig) != 64:
        return {"ok": False, "reason": "signature_malformed"}
    try:
        msg = _canonical_signing_bytes(record)
    except _UnknownFieldError:
        return {"ok": False, "reason": "binding_reject:unknown_key"}
    except _IncompleteBindingError:
        return {"ok": False, "reason": "binding_reject:incomplete"}
    if ed25519_verify(public_key, msg, sig):
        return {"ok": True}
    return {"ok": False, "reason": "binding_mismatch:signature"}


class KeychainProvider(SigningProvider):
    """keychain 서명 provider. ed25519(본 모듈) + keychain backend(lazy seam)로 sk 로드.

    ttl_seconds/pending_cap 노출(local_owner 와 동형). sk 는 메모리에만, 노출/로깅 0.
    """

    kind = "keychain"
    _DEFAULT_KEY_ID = "binggupack.trusted_approval.ed25519"

    def __init__(self, home, cfg, backend=None, key_id=None):
        self.home = home
        self.ttl_seconds = int(cfg["ttl_seconds"])
        self.pending_cap = int(cfg["pending_cap"])
        self._backend = backend if backend is not None else default_keychain_backend()
        self._key_id = key_id or self._DEFAULT_KEY_ID
        self._sk = None
        self._pk = None

    def _ensure_key(self):
        if self._pk is None:
            sk, pk = self._backend.load_or_create_signing_key(self._key_id)
            self._sk, self._pk = sk, pk

    @property
    def public_key(self):
        self._ensure_key()
        return self._pk

    def sign_record(self, record: dict) -> dict:
        """record(바인딩 필드 완비 · sig 없음) → sig(hex) 부여한 새 dict. unknown-key/누락 시 raise."""
        self._ensure_key()
        msg = _canonical_signing_bytes(record)   # 서명대상 = sig 제외 · 완전성/unknown 검증
        sig = ed25519_sign(self._sk, msg)
        out = dict(record)
        out[_SIG_KEY] = sig.hex()
        return out

    def verify_signed(self, record: dict) -> dict:
        return _verify_signed_with_pk(self.public_key, record)

    def mint_signed(self, request: dict, ttl_seconds: int, now, channel: str) -> dict:
        """approve EVENT record 생성(float→int 고정 캐스팅) 후 서명. approval_nonce = 128-bit."""
        approved_at = int(now)                       # ★ float time.time() → int 고정(결정성)
        expires_at = int(now) + int(ttl_seconds)     # ★ 정수 산술만
        record = {
            "request_id": request["request_id"],
            "protocol_version": request["protocol_version"],
            "operation": request["operation"],
            "payload_digest": request["payload_digest"],
            "ledger_id": request["ledger_id"],
            "approval_nonce": secrets.token_hex(16),
            "approved_at": approved_at,
            "expires_at": expires_at,
            "approver_channel": channel,
            "record_type": "approve",
        }
        return self.sign_record(record)


class _UnavailableSigningProvider(SigningProvider):
    """config 에 signing kind 명시됐으나 backend 미가용 — fail-closed 스텁. verify 항상 False."""

    def __init__(self, kind, cfg):
        self.kind = kind
        self.ttl_seconds = int(cfg["ttl_seconds"])
        self.pending_cap = int(cfg["pending_cap"])
        self.available = False

    @property
    def public_key(self):
        return None

    def sign_record(self, record: dict) -> dict:
        raise SigningUnavailable("signing backend unavailable (fail-closed)")

    def verify_signed(self, record: dict) -> dict:
        return {"ok": False, "reason": "signing_unavailable"}   # 절대 True 아님

    def mint_signed(self, request: dict, ttl_seconds: int, now, channel: str) -> dict:
        raise SigningUnavailable("signing backend unavailable (fail-closed)")


# ══════════════════════════════════════════════════════════════════════════════════
# 6) config / provider factory — provider_for None 무회귀 (byte-identical)
# ══════════════════════════════════════════════════════════════════════════════════
def _config_path(home):
    return os.path.join(home, "trusted_approval.json")


def load_signing_config(home):
    """서명 config 로드. 부재/disabled → None. kind 없는 기존 config 는 kind=local_owner 기본화.

    env boolean 로는 활성화되지 않는다(config 파일 신호로만). 반환 dict: enabled/kind/ttl/pending.
    """
    p = _config_path(home)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    return {
        "enabled": True,
        "kind": cfg.get("kind", "local_owner"),   # 기존 config 무회귀 = local_owner
        "ttl_seconds": int(cfg.get("ttl_seconds", 900)),
        "pending_cap": int(cfg.get("pending_cap", 64)),
    }


def signing_provider_for(home, backend=None):
    """서명 provider 반환 or None.

    - config 부재/disabled → None (오늘과 동일 · 서명 단계 완전 skip).
    - kind ∈ {local_owner, absent} → None (sig 미부여 · mint record byte-identical).
    - kind == keychain → backend 가용 시 KeychainProvider, 미가용 시 _UnavailableSigningProvider(fail-closed).
    """
    cfg = load_signing_config(home)
    if cfg is None:
        return None
    kind = cfg["kind"]
    if kind not in SIGNING_KINDS:
        return None                       # local_owner 등 → 서명 skip (무회귀)
    b = backend if backend is not None else default_keychain_backend()
    if not b.available():
        return _UnavailableSigningProvider(kind, cfg)
    return KeychainProvider(home, cfg, backend=b)


# ══════════════════════════════════════════════════════════════════════════════════
# 7) selftest — Ed25519 positive/negative KAT + float roundtrip + binding tamper KAT
# ══════════════════════════════════════════════════════════════════════════════════
def _operating_snapshot():
    """운영 ledger 파일 mtime+size 스냅샷(미접촉 실측). stat 만, read 0."""
    snap = {}
    base = os.path.expanduser("~/.binggupack")
    for name in ("ledger.sqlite", "capture_buffer.sqlite"):
        p = os.path.join(base, name)
        snap[name] = (os.stat(p).st_mtime, os.stat(p).st_size) if os.path.exists(p) else None
    return snap


# ── 고정 KAT 벡터 ─────────────────────────────────────────────────────────────
# (1) 외부 genuine RFC 8032 §7.1 Test 1 (empty message) — 이 (pk, sig) 는 이 모듈 밖(RFC 저자)이
#     생성한 진짜 벡터다. verify 가 이걸 accept 하면 base point/decompression/검증식/scalar 축약이
#     전부 RFC 상호운용이라는 강한 증거(cofactorless 식 정확성 실증).
_RFC_PK = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
_RFC_MSG = b""
_RFC_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")

# (2) 고정 시드 자기 벡터 — 실 함수(publickey/sign)로 재계산해 아래 pin 과 대조(drift 검증).
#     pin 이 어긋나면 keygen/sign 로직이 바뀐 것 → CI BLOCK. 상수는 이 모듈 함수로 산출된 값.
_PIN_SK = hashlib.sha256(b"kat-sk-2").digest()
_PIN_MSG = "빙구팩 승인 이벤트 서명 대상".encode("utf-8")
_PIN_PK = bytes.fromhex("56a304a9491791f684a4f235abd6dbb591f0e5027cc4cfddd430d095e8af74f5")
_PIN_SIG = bytes.fromhex(
    "e0ecc432b8836d51e9763b7b5d2403d6d53efee8356682edd9f12ef1a09d3cb6"
    "59fbbb39cc27209898029f871dd3d69be1b77efb877500280c5e4c6a48b93402")


def _base_record():
    return {
        "request_id": "rid-abcdef012345",
        "protocol_version": "tae-1",
        "operation": "import_edges",
        "payload_digest": "d" * 64,
        "ledger_id": "1783783039",
        "approval_nonce": "0011223344556677",
        "approved_at": 1700000000,
        "expires_at": 1700000900,
        "approver_channel": "cli_tty",
        "record_type": "approve",
    }


def _selftest():
    results = []

    def ck(name, cond):
        results.append((name, bool(cond)))

    op_before = _operating_snapshot()

    # ── K1: RFC 8032 positive 벡터 (외부 genuine) + 고정 시드 pin (drift) ───────
    # (a) 외부 genuine RFC 8032 Test 1 서명을 verify 가 accept → cofactorless 검증식/상호운용 실증.
    ck("K1a rfc8032 external vector verify accepts", ed25519_verify(_RFC_PK, _RFC_MSG, _RFC_SIG) is True)
    # (b) 고정 시드 자기 벡터: 실 함수 재계산 == pin (keygen/sign drift 검증).
    sk2, pk2, msg2 = _PIN_SK, _PIN_PK, _PIN_MSG
    ck("K1b pinned publickey == recompute", ed25519_publickey(sk2) == _PIN_PK)
    sig2 = ed25519_sign(sk2, msg2)
    ck("K1c pinned signature == recompute (drift)", sig2 == _PIN_SIG)
    ck("K1d pinned verify accepts", ed25519_verify(pk2, msg2, _PIN_SIG) is True)
    ck("K1e deterministic sign (RFC8032)", ed25519_sign(sk2, msg2) == sig2)

    # ── K2: 변조 서명 reject ────────────────────────────────────────────────────
    bad = bytearray(sig2)
    bad[0] ^= 0x01
    ck("K2a tampered R byte reject", ed25519_verify(pk2, msg2, bytes(bad)) is False)
    bad2 = bytearray(sig2)
    bad2[40] ^= 0x01
    ck("K2b tampered S byte reject", ed25519_verify(pk2, msg2, bytes(bad2)) is False)
    ck("K2c tampered message reject", ed25519_verify(pk2, msg2 + b"x", sig2) is False)

    # ── K3: 비-canonical S (S>=L) reject / malleability ────────────────────────
    r_enc = sig2[:32]
    sig_S_eq_L = r_enc + int(_L).to_bytes(32, "little")
    ck("K3a S==L reject", ed25519_verify(pk2, msg2, sig_S_eq_L) is False)
    s_orig = int.from_bytes(sig2[32:], "little")
    # S+L < L + 2^252 < 2^254 → 32바이트에 안전. malleable 변형은 S>=L 로 reject 돼야 한다.
    sig_malleable = r_enc + int(s_orig + _L).to_bytes(32, "little")
    ck("K3b malleable S+L reject", ed25519_verify(pk2, msg2, sig_malleable) is False)

    # ── K4: 잘못된 point / decompression 실패 reject ────────────────────────────
    bad_pk = int(_P).to_bytes(32, "little")           # y = p → recover_x None
    ck("K4a undecodable pubkey reject", ed25519_verify(bad_pk, msg2, sig2) is False)
    bad_R = int(_P).to_bytes(32, "little") + sig2[32:]  # R 압축값 y=p → decompress 실패
    ck("K4b undecodable R reject", ed25519_verify(pk2, msg2, bad_R) is False)
    ck("K4c short pubkey reject", ed25519_verify(pk2[:31], msg2, sig2) is False)
    ck("K4d short sig reject", ed25519_verify(pk2, msg2, sig2[:63]) is False)

    # ── K5: 전부-0 서명 reject (fail-open 방지) ─────────────────────────────────
    ck("K5 all-zero signature reject", ed25519_verify(pk2, msg2, b"\x00" * 64) is False)

    # ── K6: 잘못된 pk reject ────────────────────────────────────────────────────
    ck("K6 wrong pubkey reject", ed25519_verify(_RFC_PK, msg2, sig2) is False)

    # ── K7: float→int canonicalization roundtrip (mint→jsonl→parse→verify) ─────
    backend = InMemoryKeychainBackend(seed="kat")
    cfg = {"ttl_seconds": 900, "pending_cap": 64}
    prov = KeychainProvider("/nonexistent-home", cfg, backend=backend)
    request = {"request_id": "rid-XYZ-001", "protocol_version": "tae-1",
               "operation": "import_edges", "payload_digest": "a" * 64, "ledger_id": "1783783039"}
    rec = prov.mint_signed(request, 900, 1700000000.777, "cli_tty")   # float now
    ck("K7a approved_at is int", isinstance(rec["approved_at"], int) and rec["approved_at"] == 1700000000)
    ck("K7b expires_at is int", isinstance(rec["expires_at"], int) and rec["expires_at"] == 1700000900)
    line = json.dumps(rec, ensure_ascii=False)
    reparsed = json.loads(line)
    ck("K7c jsonl roundtrip verify OK", prov.verify_signed(reparsed).get("ok") is True)
    # 동일 record 재서명 결정성(비결정 실패 0)
    unsigned = {k: rec[k] for k in _BINDING_FIELDS}
    ck("K7d re-sign deterministic", prov.sign_record(unsigned)["sig"] == rec["sig"])
    _cb = _canonical_signing_bytes(rec)
    ck("K7e int (no float) in canonical",
       b'"approved_at":1700000000,' in _cb and b'"approved_at":1700000000.0' not in _cb)

    # ── K8: 바인딩 필드 각 1개 변조 → 전부 reject ──────────────────────────────
    signed = prov.sign_record(_base_record())
    ck("K8base signed verify OK", prov.verify_signed(signed).get("ok") is True)
    tamper_map = {
        "request_id": "rid-EVIL", "protocol_version": "tae-2", "operation": "confirm_edges",
        "payload_digest": "e" * 64, "ledger_id": "9999999999",
        "approval_nonce": "ffffffffffffffff",             # nonce 변조
        "approved_at": 1700000001, "expires_at": 1700009999,
        "approver_channel": "test_double",                # 라벨 세탁 변조
        "record_type": "revoke",
    }
    all_reject = True
    for field, newv in tamper_map.items():
        t = dict(signed)
        t[field] = newv
        v = prov.verify_signed(t)
        ok_reject = (v.get("ok") is False and v.get("reason") == "binding_mismatch:signature")
        if not ok_reject:
            all_reject = False
        results.append(("K8 tamper %s reject" % field, ok_reject))
    ck("K8 all binding tampers rejected", all_reject)
    ck("K8 channel(label-wash) detected",
       prov.verify_signed({**signed, "approver_channel": "test_double"}).get("reason") == "binding_mismatch:signature")
    ck("K8 nonce detected",
       prov.verify_signed({**signed, "approval_nonce": "0" * 16}).get("reason") == "binding_mismatch:signature")

    # ── K9: unknown-key / 누락 reject ──────────────────────────────────────────
    v_unknown = prov.verify_signed({**signed, "evil_extra": 1})
    ck("K9a unknown-key reject", v_unknown.get("reason") == "binding_reject:unknown_key")
    incomplete = dict(signed)
    del incomplete["operation"]
    ck("K9b incomplete binding reject", prov.verify_signed(incomplete).get("reason") == "binding_reject:incomplete")
    # sign_record 도 unknown-key 를 거부
    unknown_raise = False
    try:
        prov.sign_record({**_base_record(), "evil_extra": 1})
    except _UnknownFieldError:
        unknown_raise = True
    ck("K9c sign_record unknown-key raises", unknown_raise)

    # ── K10: sig 키는 서명대상 제외 (왕복) ─────────────────────────────────────
    cb1 = _canonical_signing_bytes(signed)
    cb2 = _canonical_signing_bytes({k: signed[k] for k in _BINDING_FIELDS})   # sig 없음
    ck("K10a sig excluded from canonical", cb1 == cb2)
    mutated_sig = {**signed, "sig": "00" * 64}
    ck("K10b changing sig doesn't change canonical", _canonical_signing_bytes(mutated_sig) == cb1)
    ck("K10c wrong sig value rejected",
       prov.verify_signed(mutated_sig).get("reason") == "binding_mismatch:signature")

    # ── K11: provider None 무회귀 (byte-identical · sig 미부여) ─────────────────
    ck("K11a absent config → None", signing_provider_for("/no-such-home-xyz") is None)

    import tempfile as _tf
    tmp = _tf.mkdtemp(prefix="signing_prov_")
    # local_owner config → 서명 skip
    with open(os.path.join(tmp, "trusted_approval.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "kind": "local_owner", "ttl_seconds": 900, "pending_cap": 64}, f)
    ck("K11b local_owner → signing_provider None", signing_provider_for(tmp) is None)
    # kind 없는 기존 config → local_owner 기본화 → None
    with open(os.path.join(tmp, "trusted_approval.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "ttl_seconds": 900, "pending_cap": 64}, f)
    ck("K11c no-kind config defaults local_owner → None", signing_provider_for(tmp) is None)
    # keychain config + 명시 미가용 backend → _UnavailableSigningProvider(fail-closed).
    # (Core 2 이후 default backend 는 host 플랫폼에서 가용일 수 있으므로 fail-closed 경로는
    #  미가용 backend 를 명시 주입해 host 무관 결정성으로 검증한다.)
    with open(os.path.join(tmp, "trusted_approval.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "kind": "keychain", "ttl_seconds": 900, "pending_cap": 64}, f)
    up = signing_provider_for(tmp, backend=_UnavailableKeychainBackend())   # 명시 미가용
    ck("K11d keychain + unavailable backend → Unavailable stub", isinstance(up, _UnavailableSigningProvider))
    ck("K11e Unavailable.verify_signed always False", up.verify_signed(signed).get("ok") is False)
    unavail_raise = False
    try:
        up.mint_signed(request, 900, 1700000000, "cli_tty")
    except SigningUnavailable:
        unavail_raise = True
    ck("K11f Unavailable.mint raises fail-closed", unavail_raise)
    # keychain config + fake backend 주입 → KeychainProvider
    kp = signing_provider_for(tmp, backend=InMemoryKeychainBackend(seed="kat2"))
    ck("K11g keychain + fake backend → KeychainProvider",
       isinstance(kp, KeychainProvider) and kp.kind == "keychain")
    ck("K11h SIGNING_KINDS = {keychain}", SIGNING_KINDS == frozenset({"keychain"}))

    # ── K12: 무서명 record 는 sig 키 미보유 (byte-identical unsigned) ───────────
    unsigned_only = _base_record()
    ck("K12 unsigned record has no sig key", "sig" not in unsigned_only)

    # ── K13: secret 위생 (평문 미노출) ─────────────────────────────────────────
    d = describe_secret(sk2)
    ck("K13a describe hash8 len8", len(d["sha256_hash8"]) == 8)
    ck("K13b describe keys hash+len only", set(d.keys()) == {"sha256_hash8", "length"})
    ck("K13c describe no sk leak", sk2.hex() not in str(d) and str(sk2) not in str(d))
    ck("K13d describe length matches", d["length"] == 32)

    # ── K14: 운영 ledger 미접촉 (sentinel) ─────────────────────────────────────
    op_after = _operating_snapshot()
    ck("K14 operating ledger untouched", op_before == op_after)

    # ── 집계 ────────────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    gate = "GO" if passed == total else "BLOCK"
    print("---")
    print("operating_untouched: %s" % (op_before == op_after))
    print("SIGNING-PROVIDER GATE=%s (%d/%d)" % (gate, passed, total))
    return gate, passed, total


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--selftest" in argv:
        gate, _p, _t = _selftest()
        return 0 if gate == "GO" else 1
    print("usage: python -m binggupack.safety.signing_provider --selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
