# -*- coding: utf-8 -*-
"""binggupack.safety.keychain_backend — 플랫폼 native 서명키 앵커 (PW-Core 2).

역할
  signing_provider.KeychainProvider 가 쓰는 서명키(sk)를 **OS keychain / credential manager**
  (모델의 FS 도구가 파일로 못 읽는 사용자-보호 스토어)에 앵커한다. sk 는 keychain 안에서만 살고,
  consume 은 공개키(pk)만으로 Ed25519 검증한다(sk 미노출). 백엔드는 win32(DPAPI+Credential Manager),
  darwin(security(1) generic-password), linux(secret-service/D-Bus, secret-tool) 3분기 + fallback.

정직 라벨 (★ 절대 준수 — 이 3문장은 attack_demo/RFC/SECURITY/PR 에도 동일 명시)
  1) L2(keychain 서명)는 owner 머신(Shell/FS MCP 병재)에서 위조를 못 막는다 — 같은 uid 셸이 keychain
     에서 sk 를 로드해 스스로 서명하면 "같은 머신 키 = 보안 연극"이 성립한다.
  2) config 의 kind 는 모델-writable 평문이라, kind:local_owner 한 줄로 서명 검증을 통째 skip 시킬 수
     있다(R-B6 · P1-B2 · out-of-scope). 즉 로컬 config 자체가 신뢰 경계 밖이다.
  3) L2 의 실질 값은 hosted/locked 배포(모델이 셸/keychain/config 를 못 건드리는 환경)에서만 나온다.
  → 이 모듈은 keychain 을 "L1 전부 + FS-only 위조 차단"으로만 쓴다. 코드/셸 실행 공격은 미차단이며
     로컬에서 "위조 차단/보안 강화"라고 서술하지 않는다(RFC §2.1 · SECURITY §22 계승).

fail-closed
  백엔드 감지/가용 실패(headless linux·keychain 부재·미지원 플랫폼) → available()=False → signing_provider
  가 _UnavailableSigningProvider 로 귀결(L0). L1 평문으로의 silent 강등은 없다("안 써짐"이 최악).

import-time 부작용 0 (영구금지 · 신규모듈 게이트)
  top-level 에서 ctypes.windll/crypt32/advapi32·subprocess security·D-Bus/secret-service probe 를 절대
  하지 않는다. 모든 OS 접근(ctypes/subprocess/shutil/secrets/ed25519)은 함수 내부 lazy import 로만.
  이 모듈은 anywhere/core 벤더 set 에 추가되지 않는다.

backend 주입 seam
  get_backend(inject=None): inject 지정 시 그대로 반환(테스트·attack_demo 가 in-memory fake 강제 —
  실 OS keychain·실 ~/.binggupack 미접촉). inject None(운영) 시에만 플랫폼 백엔드를 감지/반환.

secret 위생 (영구금지 18)
  sk/키 원문을 print/log/repr 하지 않는다. describe_secret() 이 sha256 hash8 + 길이만 반환한다.

CLI: python -m binggupack.safety.keychain_backend --selftest   ->  GATE=GO | GATE=BLOCK
     (네트워크 0 · 운영 ~/.binggupack·실 keychain 미접촉 · in-memory fake 주입만)
"""
from __future__ import annotations

import hashlib
import os
import sys

# Core 1(signing_provider) 의존 — 순수 stdlib, OS probe 0 · 부작용 0 (top-level import 안전).
# signing_provider 는 keychain_backend 를 lazy(함수 내부)로만 import 하므로 순환 없음.
from binggupack.safety.signing_provider import KeychainBackend as _SPKeychainBackend
from binggupack.safety.signing_provider import ed25519_publickey as _ed25519_publickey

# keychain 항목 네임스페이스 (서비스명). 실 스토어에 이 접두로만 접근한다.
_SERVICE = "BingguPack.trusted_approval"
_KEY_LEN = 32   # Ed25519 seed(sk) 바이트 길이


# ══════════════════════════════════════════════════════════════════════════════════
# 0) secret 위생 — describe_secret (hag_keyring 계승 · 평문 0)
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


class KeychainError(RuntimeError):
    """keychain 백엔드 연산 실패(손상 항목·저장/조회 오류). 평문은 절대 담지 않는다."""


def _publickey(sk: bytes) -> bytes:
    """sk(32B) → Ed25519 압축 공개키(32B). Core 1 의 순수 stdlib ed25519(OS 접근 0)."""
    return _ed25519_publickey(sk)


# ══════════════════════════════════════════════════════════════════════════════════
# 1) 백엔드 계약 + fail-closed 스텁
# ══════════════════════════════════════════════════════════════════════════════════
class _KeychainBackendBase(_SPKeychainBackend):
    """서명키 저장/로드 백엔드 계약(Core 1 KeychainBackend 상속). 실 OS 접근은 메서드 내부 lazy import 로만."""

    kind = "abstract"

    def peek_key_present(self, key_id: str):  # pragma: no cover - 기본
        """읽기-전용 존재 확인(sentinel 용). 항목 생성/수정 0. 미지원/미가용 → None."""
        return None


class _UnavailableKeychainBackend(_KeychainBackendBase):
    """미지원 플랫폼·keychain 부재·headless → 항상 available=False(L0 fail-closed).

    load_or_create 는 raise 한다 — L1 평문으로의 silent 강등을 금지한다(fail-closed).
    """

    kind = "unavailable"

    def __init__(self, reason: str = "backend_unavailable"):
        self.reason = reason

    def available(self) -> bool:
        return False

    def load_or_create_signing_key(self, key_id: str):
        raise KeychainError("keychain backend unavailable: %s" % self.reason)

    def peek_key_present(self, key_id: str):
        return None


# ══════════════════════════════════════════════════════════════════════════════════
# 2) win32 — DPAPI(CryptProtectData) + Credential Manager(CredWrite/CredRead)
# ══════════════════════════════════════════════════════════════════════════════════
class _Win32DpapiCredBackend(_KeychainBackendBase):
    """sk 를 DPAPI(사용자 컨텍스트)로 암호화해 Windows Credential Manager 에 저장한다.

    파일이 아니므로 모델의 FS 도구로는 못 읽는다(L2 = FS-only 차단). 같은 uid 셸/네이티브 코드는
    같은 DPAPI/Cred API 를 호출할 수 있어 미차단(정직 라벨 1). 모든 ctypes 는 메서드 내부 lazy.
    """

    kind = "win32_dpapi_cred"
    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def _target(self, key_id: str) -> str:
        return "%s:%s" % (_SERVICE, key_id)

    def available(self) -> bool:
        try:
            if sys.platform != "win32":
                return False
            import ctypes
            # DLL 로드 자체는 부작용 없는 가용성 확인(항목 접근 0).
            ctypes.WinDLL("crypt32")
            ctypes.WinDLL("advapi32")
            return True
        except Exception:
            return False

    # ── ctypes 구조체/함수 바인딩(내부 lazy) ────────────────────────────────────
    def _bind(self):
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return ctypes, wintypes, DATA_BLOB, CREDENTIAL, crypt32, advapi32, kernel32

    def _dpapi_protect(self, ctx, raw: bytes) -> bytes:
        ctypes, wintypes, DATA_BLOB, _CRED, crypt32, _adv, kernel32 = ctx
        buf_in = ctypes.create_string_buffer(raw, len(raw))   # 호출 동안 참조 유지(GC 안전)
        blob_in = DATA_BLOB(len(raw), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            raise KeychainError("CryptProtectData failed (err=%d)" % ctypes.get_last_error())
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def _dpapi_unprotect(self, ctx, enc: bytes) -> bytes:
        ctypes, wintypes, DATA_BLOB, _CRED, crypt32, _adv, kernel32 = ctx
        buf_in = ctypes.create_string_buffer(enc, len(enc))   # 호출 동안 참조 유지(GC 안전)
        blob_in = DATA_BLOB(len(enc), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            raise KeychainError("CryptUnprotectData failed (err=%d)" % ctypes.get_last_error())
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)

    def _cred_read(self, ctx, target: str):
        """존재 시 CredentialBlob(bytes) 반환, 부재 시 None."""
        ctypes, wintypes, _BLOB, CREDENTIAL, _c32, advapi32, kernel32 = ctx
        pcred = ctypes.POINTER(CREDENTIAL)()
        ok = advapi32.CredReadW(target, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pcred))
        if not ok:
            return None
        try:
            cred = pcred.contents
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        finally:
            advapi32.CredFree(pcred)

    def _cred_write(self, ctx, target: str, blob: bytes):
        ctypes, wintypes, _BLOB, CREDENTIAL, _c32, advapi32, _k = ctx
        buf = ctypes.create_string_buffer(blob, len(blob))
        cred = CREDENTIAL()
        cred.Flags = 0
        cred.Type = self._CRED_TYPE_GENERIC
        cred.TargetName = target
        cred.Comment = None
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        cred.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = target
        ok = advapi32.CredWriteW(ctypes.byref(cred), 0)
        if not ok:
            raise KeychainError("CredWriteW failed (err=%d)" % ctypes.get_last_error())

    def peek_key_present(self, key_id: str):
        try:
            if not self.available():
                return None
            ctx = self._bind()
            return self._cred_read(ctx, self._target(key_id)) is not None
        except Exception:
            return None

    def load_or_create_signing_key(self, key_id: str):
        import secrets
        ctx = self._bind()
        target = self._target(key_id)
        enc = self._cred_read(ctx, target)
        if enc is not None:
            sk = self._dpapi_unprotect(ctx, enc)
            if len(sk) != _KEY_LEN:
                raise KeychainError("stored signing key corrupt (len mismatch)")
            return sk, _publickey(sk)
        sk = secrets.token_bytes(_KEY_LEN)
        enc = self._dpapi_protect(ctx, sk)
        self._cred_write(ctx, target, enc)
        return sk, _publickey(sk)


# ══════════════════════════════════════════════════════════════════════════════════
# 3) darwin — security(1) generic-password
# ══════════════════════════════════════════════════════════════════════════════════
class _DarwinSecurityBackend(_KeychainBackendBase):
    """macOS Keychain generic-password 에 sk(hex)를 저장한다. `security` CLI 를 subprocess 로 호출.

    FS 도구로는 못 읽는다(L2). 같은 uid 셸이 `security find-generic-password` 를 부르면 읽힌다(미차단).
    (정직: add-generic-password 는 -w 값을 argv 로 받아 프로세스 목록 노출 여지 — same-machine L2 는
     이미 하드 통제가 아니므로 이 노출은 위협모델을 바꾸지 않는다. 셸 병재면 어차피 sk 접근 가능.)
    """

    kind = "darwin_security"

    def available(self) -> bool:
        try:
            if sys.platform != "darwin":
                return False
            import shutil
            return shutil.which("security") is not None
        except Exception:
            return False

    def _find(self, key_id: str):
        import subprocess
        r = subprocess.run(
            ["security", "find-generic-password", "-s", _SERVICE, "-a", key_id, "-w"],
            capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return r.stdout.strip()

    def peek_key_present(self, key_id: str):
        try:
            if not self.available():
                return None
            return self._find(key_id) is not None
        except Exception:
            return None

    def load_or_create_signing_key(self, key_id: str):
        import secrets
        import subprocess
        hexval = self._find(key_id)
        if hexval:
            try:
                sk = bytes.fromhex(hexval)
            except ValueError:
                raise KeychainError("stored signing key corrupt (hex)")
            if len(sk) != _KEY_LEN:
                raise KeychainError("stored signing key corrupt (len mismatch)")
            return sk, _publickey(sk)
        sk = secrets.token_bytes(_KEY_LEN)
        r = subprocess.run(
            ["security", "add-generic-password", "-s", _SERVICE, "-a", key_id, "-w", sk.hex(), "-U"],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise KeychainError("security add-generic-password failed")
        return sk, _publickey(sk)


# ══════════════════════════════════════════════════════════════════════════════════
# 4) linux — secret-service(D-Bus) via secret-tool, 존재 시에만
# ══════════════════════════════════════════════════════════════════════════════════
class _LinuxSecretServiceBackend(_KeychainBackendBase):
    """freedesktop secret-service(D-Bus) 에 sk(hex)를 저장한다. libsecret 의 `secret-tool` 사용.

    headless(D-Bus 세션 부재)·secret-tool 미설치 → available False(L0 fail-closed). `secret-tool store`
    는 secret 을 stdin 으로 받아 argv 노출이 없다. 같은 uid 셸의 `secret-tool lookup` 은 읽힘(미차단).
    """

    kind = "linux_secret_service"

    def _attrs(self, key_id: str):
        return ["application", _SERVICE, "key_id", key_id]

    def available(self) -> bool:
        try:
            if not sys.platform.startswith("linux"):
                return False
            import shutil
            if shutil.which("secret-tool") is None:
                return False
            # D-Bus 세션 신호가 없으면(headless CI) secret-service 없음 → fail-closed.
            if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
                return False
            return True
        except Exception:
            return False

    def _lookup(self, key_id: str):
        import subprocess
        r = subprocess.run(["secret-tool", "lookup", *self._attrs(key_id)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return None
        out = r.stdout.strip()
        return out or None

    def peek_key_present(self, key_id: str):
        try:
            if not self.available():
                return None
            return self._lookup(key_id) is not None
        except Exception:
            return None

    def load_or_create_signing_key(self, key_id: str):
        import secrets
        import subprocess
        hexval = self._lookup(key_id)
        if hexval:
            try:
                sk = bytes.fromhex(hexval)
            except ValueError:
                raise KeychainError("stored signing key corrupt (hex)")
            if len(sk) != _KEY_LEN:
                raise KeychainError("stored signing key corrupt (len mismatch)")
            return sk, _publickey(sk)
        sk = secrets.token_bytes(_KEY_LEN)
        r = subprocess.run(
            ["secret-tool", "store", "--label=BingguPack trusted_approval signing key",
             *self._attrs(key_id)],
            input=sk.hex(), capture_output=True, text=True)
        if r.returncode != 0:
            raise KeychainError("secret-tool store failed")
        return sk, _publickey(sk)


# ══════════════════════════════════════════════════════════════════════════════════
# 5) 감지 + 주입 seam
# ══════════════════════════════════════════════════════════════════════════════════
def _detect_backend() -> _KeychainBackendBase:
    """플랫폼 백엔드 감지. 가용하면 반환, 아니면 _UnavailableKeychainBackend(fail-closed).

    이 함수를 **호출할 때만** available() 이 OS 를 probe 한다(import-time 부작용 0). 각 backend 의
    __init__ 은 OS 를 건드리지 않는다 — 인스턴스화만으로는 어떤 keychain 도 열리지 않는다.
    """
    if sys.platform == "win32":
        b: _KeychainBackendBase = _Win32DpapiCredBackend()
    elif sys.platform == "darwin":
        b = _DarwinSecurityBackend()
    elif sys.platform.startswith("linux"):
        b = _LinuxSecretServiceBackend()
    else:
        return _UnavailableKeychainBackend("unsupported_platform:%s" % sys.platform)
    try:
        if b.available():
            return b
    except Exception:
        return _UnavailableKeychainBackend("availability_probe_error")
    return _UnavailableKeychainBackend("backend_unavailable")


def get_backend(inject=None) -> _KeychainBackendBase:
    """서명키 백엔드 반환.

    - inject 지정(테스트·attack_demo·주입 seam) → 그대로 반환. 실 OS keychain·실 ~/.binggupack 미접촉.
    - inject None(운영 · signing_provider.default_keychain_backend 경로) → 플랫폼 백엔드 감지.
      감지/가용 실패 → _UnavailableKeychainBackend(available False) → signing_provider 가 fail-closed.
    """
    if inject is not None:
        return inject
    return _detect_backend()


# ══════════════════════════════════════════════════════════════════════════════════
# 6) selftest — 주입 seam · fail-closed · 서명 통합 KAT · Ed25519 negative · sentinel
# ══════════════════════════════════════════════════════════════════════════════════
def _operating_snapshot():
    """운영 ledger 파일 mtime+size 스냅샷(미접촉 실측). stat 만, read 0."""
    snap = {}
    base = os.path.expanduser("~/.binggupack")
    for name in ("ledger.sqlite", "capture_buffer.sqlite"):
        p = os.path.join(base, name)
        snap[name] = (os.stat(p).st_mtime, os.stat(p).st_size) if os.path.exists(p) else None
    return snap


def _real_keychain_sentinel(key_id: str):
    """실 keychain 의 우리 항목 존재 여부(읽기-전용). 미가용 → None. 이 함수는 write 0."""
    try:
        real = _detect_backend()
        return {"available": real.available(), "present": real.peek_key_present(key_id)}
    except Exception:
        return {"available": None, "present": None}


def _selftest():
    from binggupack.safety import signing_provider as sp

    results = []

    def ck(name, cond):
        results.append((name, bool(cond)))

    # ── sentinel: 실 운영 ledger + 실 keychain 우리 항목 (전 구간 불변 단언) ─────────
    op_before = _operating_snapshot()
    kc_key_id = "binggupack.selftest.keychain_backend.DO_NOT_CREATE"
    kc_before = _real_keychain_sentinel(kc_key_id)

    # ── S0: import-time 부작용 0 — OS 라이브러리가 top-level 로 안 들어왔는지 ──────────
    mod = sys.modules[__name__]
    modvars = set(vars(mod).keys())
    ck("S0a no top-level ctypes/subprocess/shutil/secrets binding",
       not ({"ctypes", "subprocess", "shutil", "secrets"} & modvars))
    ck("S0b top-level imports stdlib only (hashlib/os/sys)",
       {"hashlib", "os", "sys"} <= modvars)

    # ── S1: 주입 seam — inject 지정 시 그대로 반환(실 keychain 미접촉) ────────────────
    fake = sp.InMemoryKeychainBackend(seed="kc-core2")
    ck("S1a get_backend(inject=fake) returns the fake", get_backend(inject=fake) is fake)
    ck("S1b get_backend(inject=None) is not the fake", get_backend() is not fake)

    # ── S2: fake 결정성 + pk == ed25519_publickey(sk) ───────────────────────────────
    sk, pk = fake.load_or_create_signing_key("kid-A")
    ck("S2a sk is 32 bytes", isinstance(sk, (bytes, bytearray)) and len(sk) == _KEY_LEN)
    ck("S2b pk is 32 bytes", isinstance(pk, (bytes, bytearray)) and len(pk) == 32)
    ck("S2c pk == ed25519_publickey(sk)", bytes(pk) == sp.ed25519_publickey(bytes(sk)))
    sk2, pk2 = fake.load_or_create_signing_key("kid-A")
    ck("S2d same key_id → same sk/pk (idempotent)", bytes(sk2) == bytes(sk) and bytes(pk2) == bytes(pk))
    fake_seed2 = sp.InMemoryKeychainBackend(seed="kc-core2")
    sk3, _ = fake_seed2.load_or_create_signing_key("kid-A")
    ck("S2e same seed+key_id deterministic across instances", bytes(sk3) == bytes(sk))

    # ── S3: 통합 KAT — 주입 backend 로 KeychainProvider 서명 왕복(float→int·완전성·tamper) ─
    import json
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kc_backend_")
    with open(os.path.join(tmp, "trusted_approval.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "kind": "keychain", "ttl_seconds": 900, "pending_cap": 64}, f)
    prov = sp.signing_provider_for(tmp, backend=get_backend(inject=fake))
    ck("S3a keychain config + injected backend → KeychainProvider",
       isinstance(prov, sp.KeychainProvider) and prov.kind == "keychain")
    request = {"request_id": "rid-KC-001", "protocol_version": "tae-1", "operation": "import_edges",
               "payload_digest": "b" * 64, "ledger_id": "1783783039"}
    rec = prov.mint_signed(request, 900, 1700000000.777, "cli_tty")   # float now
    ck("S3b approved_at is int (float→int canonicalization)",
       isinstance(rec["approved_at"], int) and rec["approved_at"] == 1700000000)
    ck("S3c expires_at is int", isinstance(rec["expires_at"], int) and rec["expires_at"] == 1700000900)
    reparsed = json.loads(json.dumps(rec, ensure_ascii=False))   # jsonl 왕복
    ck("S3d jsonl roundtrip verify OK", prov.verify_signed(reparsed).get("ok") is True)
    ck("S3e re-sign deterministic",
       prov.sign_record({k: rec[k] for k in rec if k != "sig"})["sig"] == rec["sig"])
    # 각 바인딩 필드 1개 변조 → 전부 reject(binding_mismatch:signature)
    tamper_map = {
        "request_id": "rid-EVIL", "protocol_version": "tae-2", "operation": "confirm_edges",
        "payload_digest": "e" * 64, "ledger_id": "9999999999",
        "approval_nonce": "0" * 32, "approved_at": rec["approved_at"] + 1,
        "expires_at": rec["expires_at"] + 1, "approver_channel": "test_double",
        "record_type": "revoke",
    }
    all_reject = all(
        prov.verify_signed({**rec, k: v}).get("reason") == "binding_mismatch:signature"
        for k, v in tamper_map.items())
    ck("S3f every binding-field tamper rejected (incl approver_channel/nonce)", all_reject)
    ck("S3g unknown-key reject",
       prov.verify_signed({**rec, "evil_extra": 1}).get("reason") == "binding_reject:unknown_key")

    # ── S4: Ed25519 negative KAT (fail-open 차단) ────────────────────────────────────
    v_sk, v_pk = fake.load_or_create_signing_key("kid-V")
    msg = b"binggupack keychain backend negative KAT"
    good = sp.ed25519_sign(bytes(v_sk), msg)
    ck("S4a positive verify accepts", sp.ed25519_verify(bytes(v_pk), msg, good) is True)
    bad = bytearray(good); bad[10] ^= 0x01
    ck("S4b tampered signature reject", sp.ed25519_verify(bytes(v_pk), msg, bytes(bad)) is False)
    L = sp._L
    ck("S4c non-canonical S (S==L) reject",
       sp.ed25519_verify(bytes(v_pk), msg, good[:32] + int(L).to_bytes(32, "little")) is False)
    ck("S4d all-zero signature reject", sp.ed25519_verify(bytes(v_pk), msg, b"\x00" * 64) is False)
    ck("S4e wrong pubkey reject", sp.ed25519_verify(bytes(pk), msg, good) is False)
    ck("S4f undecodable pubkey reject",
       sp.ed25519_verify(int(sp._P).to_bytes(32, "little"), msg, good) is False)

    # ── S5: fail-closed — Unavailable backend / 미지원 플랫폼 ─────────────────────────
    ua = _UnavailableKeychainBackend("test")
    ck("S5a unavailable.available() is False", ua.available() is False)
    raised = False
    try:
        ua.load_or_create_signing_key("kid")
    except KeychainError:
        raised = True
    ck("S5b unavailable.load raises (no silent L1 downgrade)", raised)
    up = sp.signing_provider_for(tmp, backend=ua)
    ck("S5c keychain config + unavailable backend → _UnavailableSigningProvider fail-closed",
       up.verify_signed(rec).get("ok") is False)

    # ── S6: get_backend(None) 운영 경로 — 실-or-Unavailable, mutation 0 ───────────────
    real = get_backend()
    ck("S6a real backend has available()/load_or_create", hasattr(real, "available") and hasattr(real, "load_or_create_signing_key"))
    ck("S6b real backend availability is bool", isinstance(real.available(), bool))
    ck("S6c real backend is not the fake", real is not fake)
    ck("S6d unavailable → fail-closed unavailable kind or platform backend",
       real.available() or real.kind == "unavailable")

    # ── S7: provider None 무회귀 (local_owner / no-kind → 서명 skip · byte-identical) ─
    with open(os.path.join(tmp, "trusted_approval.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "kind": "local_owner", "ttl_seconds": 900, "pending_cap": 64}, f)
    ck("S7a local_owner → signing_provider None", sp.signing_provider_for(tmp) is None)
    with open(os.path.join(tmp, "trusted_approval.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "ttl_seconds": 900, "pending_cap": 64}, f)
    ck("S7b no-kind config defaults local_owner → None", sp.signing_provider_for(tmp) is None)
    ck("S7c absent config → None", sp.signing_provider_for("/no-such-home-xyz") is None)

    # ── S8: secret 위생 — describe_secret (평문 미노출) ──────────────────────────────
    d = describe_secret(bytes(sk))
    ck("S8a describe keys hash+len only", set(d.keys()) == {"sha256_hash8", "length"})
    ck("S8b describe hash8 len8", len(d["sha256_hash8"]) == 8)
    ck("S8c describe length matches", d["length"] == _KEY_LEN)
    ck("S8d describe no sk leak", bytes(sk).hex() not in str(d) and str(bytes(sk)) not in str(d))

    # ── S9: 운영 ledger + 실 keychain 우리 항목 미접촉(sentinel 불변) ─────────────────
    op_after = _operating_snapshot()
    kc_after = _real_keychain_sentinel(kc_key_id)
    ck("S9a operating ledger untouched", op_before == op_after)
    ck("S9b real keychain sentinel unchanged (our key_id not created)", kc_before == kc_after)
    ck("S9c our selftest key_id absent in real keychain",
       kc_after.get("present") in (False, None))

    import shutil as _sh
    _sh.rmtree(tmp, ignore_errors=True)

    # ── 집계 ────────────────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    gate = "GO" if passed == total else "BLOCK"
    print("---")
    print("operating_untouched: %s" % (op_before == op_after))
    print("real_keychain_untouched: %s" % (kc_before == kc_after))
    print("KEYCHAIN-BACKEND GATE=%s (%d/%d)" % (gate, passed, total))
    return gate, passed, total


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--selftest" in argv:
        gate, _p, _t = _selftest()
        return 0 if gate == "GO" else 1
    print("usage: python -m binggupack.safety.keychain_backend --selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
