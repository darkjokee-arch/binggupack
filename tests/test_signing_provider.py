# -*- coding: utf-8 -*-
"""L2 서명 계층 단언 — signing_provider · keychain_backend · trusted_approval 서명 경로.

이 테스트는 "로컬에서 위조를 막는다"를 주장하지 않는다(정직 라벨). L2(keychain)는 owner 머신에서
같은 uid 셸이 keychain sk 를 로드해 스스로 서명하면 뚫린다("같은 머신 키 = 보안 연극"), config 의
kind 는 모델-writable 평문이라 kind:local_owner 한 줄로 서명 검증을 통째 skip 시킬 수 있다. 여기서
검증하는 것은 (1) Ed25519 가 fail-open 하지 않는다 (2) 서명 대상이 모든 바인딩 필드를 포함한다
(3) float→int canonicalization 이 결정적이다 (4) provider None/local_owner 는 오늘과 byte-identical
이다 (5) backend 미가용이면 fail-closed 다 — 즉 '정직한 감사 흔적'의 무결성뿐이다.

전부 in-memory fake backend + temp home 격리. 운영 ~/.binggupack ledger + 실 keychain 미접촉을
sentinel(mtime/size · read-only peek)로 단언한다. 실 keychain 은 어떤 테스트도 write 하지 않는다.
Windows pytest foreground 실행 전제(background/255 회피). PII/시크릿 리터럴 0.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from binggupack.safety import keychain_backend as kb  # noqa: E402
from binggupack.safety import signing_provider as sp  # noqa: E402
from binggupack.safety import trusted_approval as ta  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════════
# sentinel — 운영 홈 ledger + 실 keychain 우리 항목 (전 세션 불변 단언)
# ══════════════════════════════════════════════════════════════════════════════════
_OP_HOME = os.path.join(os.path.expanduser("~"), ".binggupack")
_OP_FILES = ("ledger.sqlite", "capture_buffer.sqlite", "approvals.jsonl")
_PROD_KEY_ID = "binggupack.trusted_approval.ed25519"


def _operating_snapshot() -> dict:
    snap = {}
    for name in _OP_FILES:
        p = os.path.join(_OP_HOME, name)
        snap[name] = (os.stat(p).st_mtime, os.stat(p).st_size) if os.path.exists(p) else None
    return snap


def _real_keychain_present(key_id: str):
    """실 keychain 의 우리 항목 존재 여부(읽기-전용 peek). write 0. 미가용 → None."""
    try:
        real = kb.get_backend()          # inject 없음 → 플랫폼 백엔드(운영 경로)
        return real.peek_key_present(key_id)
    except Exception:
        return None


@pytest.fixture(autouse=True)
def _operating_untouched():
    """모든 테스트 전후로 운영 홈 ledger + 실 keychain 우리 항목이 불변임을 강제한다."""
    op_before = _operating_snapshot()
    kc_before = _real_keychain_present(_PROD_KEY_ID)
    yield
    assert _operating_snapshot() == op_before, "운영 홈 ledger 가 변경됨(격리 실패)"
    assert _real_keychain_present(_PROD_KEY_ID) == kc_before, "실 keychain 항목이 변경됨(격리 실패)"


def _fake(seed: str = "signing-test") -> sp.InMemoryKeychainBackend:
    return sp.InMemoryKeychainBackend(seed=seed)


def _write_config(home: str, kind: str | None) -> None:
    cfg: dict = {"enabled": True, "ttl_seconds": 900, "pending_cap": 64}
    if kind is not None:
        cfg["kind"] = kind
    with open(ta.config_path(home), "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def _keychain_home(tmp_path, monkeypatch, seed: str = "kc-home") -> str:
    """kind=keychain config + default backend 를 공유 in-memory fake 로 monkeypatch 한 home.

    mint_approval/verify_event 는 provider_for→signing_provider_for(backend 미주입) 경로라 실
    default_keychain_backend 를 부른다. 여기서 그걸 단일 fake 인스턴스로 치환해 (a) 실 keychain
    미접촉 (b) mint/verify 가 같은 sk 를 공유(결정성)하게 한다."""
    home = str(tmp_path)
    _write_config(home, "keychain")
    shared = _fake(seed)
    monkeypatch.setattr(sp, "default_keychain_backend", lambda: shared)
    return home


# ══════════════════════════════════════════════════════════════════════════════════
# 1) Ed25519 KAT — positive(RFC 8032 외부 벡터) + negative 전량 reject (fail-open 0)
# ══════════════════════════════════════════════════════════════════════════════════
# RFC 8032 §7.1 Test 1 (empty msg) + Test 2 (1바이트 0x72). 이 (pk, sig)는 이 모듈 밖(RFC 저자)이
# 만든 진짜 벡터라, verify 가 accept 하면 base point/decompression/scalar 축약/검증식이 상호운용.
_RFC1_PK = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
_RFC1_MSG = b""
_RFC1_SIG = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
_RFC2_PK = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
_RFC2_MSG = bytes.fromhex("72")
_RFC2_SIG = bytes.fromhex(
    "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
    "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")


@pytest.mark.parametrize("pk,msg,sig", [
    (_RFC1_PK, _RFC1_MSG, _RFC1_SIG),
    (_RFC2_PK, _RFC2_MSG, _RFC2_SIG),
])
def test_ed25519_positive_rfc8032_vectors(pk, msg, sig):
    """외부 genuine RFC 8032 벡터를 accept — cofactorless 검증식/상호운용 실증."""
    assert sp.ed25519_verify(pk, msg, sig) is True


def _fresh_key(seed: str = "neg-kat"):
    sk, pk = _fake(seed).load_or_create_signing_key("kid-neg")
    return bytes(sk), bytes(pk)


def test_ed25519_negative_kat_all_reject():
    """RFC 8032 negative — 하나라도 accept 하면 fail-open(최악). 전량 reject 단언."""
    sk, pk = _fresh_key()
    msg = b"binggupack L2 negative KAT payload"
    good = sp.ed25519_sign(sk, msg)
    assert sp.ed25519_verify(pk, msg, good) is True   # baseline positive

    negatives: dict[str, tuple] = {}

    # (a) 변조 서명 — R 바이트 / S 바이트 각각
    tr = bytearray(good)
    tr[0] ^= 0x01
    negatives["tampered_R_byte"] = (pk, msg, bytes(tr))
    ts = bytearray(good)
    ts[40] ^= 0x01
    negatives["tampered_S_byte"] = (pk, msg, bytes(ts))
    negatives["tampered_message"] = (pk, msg + b"x", good)

    # (b) 비-canonical S (S>=L) reject
    r_enc = good[:32]
    negatives["S_equals_L"] = (pk, msg, r_enc + int(sp._L).to_bytes(32, "little"))
    s_orig = int.from_bytes(good[32:], "little")
    negatives["malleable_S_plus_L"] = (pk, msg, r_enc + int(s_orig + sp._L).to_bytes(32, "little"))

    # (c) 잘못된 point / decompression 실패
    undecodable = int(sp._P).to_bytes(32, "little")     # y=p → recover_x None
    negatives["undecodable_pubkey"] = (undecodable, msg, good)
    negatives["undecodable_R"] = (pk, msg, undecodable + good[32:])

    # (d) malleability / 전부-0 서명
    negatives["all_zero_signature"] = (pk, msg, b"\x00" * 64)

    # (e) 잘못된 pk
    _sk2, pk2 = _fresh_key("neg-kat-2")
    negatives["wrong_pubkey"] = (pk2, msg, good)

    # 길이 오류(방어)
    negatives["short_pubkey"] = (pk[:31], msg, good)
    negatives["short_signature"] = (pk, msg, good[:63])
    negatives["long_signature"] = (pk, msg, good + b"\x00")

    for name, (p, m, s) in negatives.items():
        assert sp.ed25519_verify(p, m, s) is False, "fail-open: %s 를 accept 함" % name


def test_ed25519_deterministic_sign():
    """RFC 8032 결정적 서명 — 같은 (sk,msg) 재서명 동일(비결정 0)."""
    sk, _pk = _fresh_key("det")
    msg = "빙구팩 승인 이벤트".encode("utf-8")
    assert sp.ed25519_sign(sk, msg) == sp.ed25519_sign(sk, msg)


# ══════════════════════════════════════════════════════════════════════════════════
# 2) 서명 대상 완전성 — 각 바인딩 필드 변조 reject · unknown-key reject · sig 제외 왕복
# ══════════════════════════════════════════════════════════════════════════════════
_BINDING_TAMPERS = {
    "request_id": "rid-EVIL",
    "protocol_version": "tae-2",
    "operation": "confirm_edges",
    "payload_digest": "e" * 64,
    "ledger_id": "9999999999",
    "approval_nonce": "0" * 32,
    "approved_at": 1700000001,
    "expires_at": 1700009999,
    "approver_channel": "test_double",   # 라벨 세탁
    "record_type": "revoke",
}


def _provider(seed: str = "binding") -> sp.KeychainProvider:
    cfg = {"ttl_seconds": 900, "pending_cap": 64}
    return sp.KeychainProvider("/nonexistent-home", cfg, backend=_fake(seed))


def _base_record() -> dict:
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


def test_signed_record_verifies():
    prov = _provider()
    signed = prov.sign_record(_base_record())
    assert prov.verify_signed(signed).get("ok") is True
    assert isinstance(signed["sig"], str) and len(bytes.fromhex(signed["sig"])) == 64


@pytest.mark.parametrize("field,newv", list(_BINDING_TAMPERS.items()))
def test_each_binding_field_tamper_rejected(field, newv):
    """바인딩 필드 1개 변조 → binding_mismatch:signature. approver_channel(라벨세탁)·nonce 포함."""
    prov = _provider()
    signed = prov.sign_record(_base_record())
    tampered = dict(signed)
    tampered[field] = newv
    v = prov.verify_signed(tampered)
    assert v.get("ok") is False
    assert v.get("reason") == "binding_mismatch:signature", "%s 변조 미검출" % field


def test_unknown_key_injection_rejected():
    """미서명 여분키(unknown-key) 주입 → binding_reject:unknown_key. sign_record 도 거부."""
    prov = _provider()
    signed = prov.sign_record(_base_record())
    assert prov.verify_signed({**signed, "evil_extra": 1}).get("reason") == "binding_reject:unknown_key"
    with pytest.raises(sp._UnknownFieldError):
        prov.sign_record({**_base_record(), "evil_extra": 1})


def test_incomplete_binding_rejected():
    prov = _provider()
    signed = prov.sign_record(_base_record())
    incomplete = dict(signed)
    del incomplete["operation"]
    assert prov.verify_signed(incomplete).get("reason") == "binding_reject:incomplete"


def test_sig_excluded_from_signing_bytes_roundtrip():
    """sig 키는 서명 대상 제외 — sig 값 변경이 canonical bytes 를 안 바꾸고, 서명대상 왕복 동일."""
    prov = _provider()
    signed = prov.sign_record(_base_record())
    cb_with_sig = sp._canonical_signing_bytes(signed)
    cb_no_sig = sp._canonical_signing_bytes({k: signed[k] for k in signed if k != "sig"})
    assert cb_with_sig == cb_no_sig
    assert sp._canonical_signing_bytes({**signed, "sig": "00" * 64}) == cb_with_sig
    # 서명대상은 같지만 sig 값이 틀리면 검증은 실패(내용 위조는 못 함)
    assert prov.verify_signed({**signed, "sig": "00" * 64}).get("reason") == "binding_mismatch:signature"


def test_missing_or_malformed_signature_rejected():
    prov = _provider()
    base = _base_record()
    assert prov.verify_signed(base).get("reason") == "signature_missing"
    assert prov.verify_signed({**base, "sig": "zz"}).get("reason") == "signature_malformed"
    assert prov.verify_signed({**base, "sig": "00" * 10}).get("reason") == "signature_malformed"


# ══════════════════════════════════════════════════════════════════════════════════
# 3) float→int canonicalization 결정성 — mint→jsonl→parse→verify 왕복
# ══════════════════════════════════════════════════════════════════════════════════
def test_mint_casts_float_now_to_int():
    prov = _provider("float")
    request = {"request_id": "rid-XYZ", "protocol_version": "tae-1", "operation": "import_edges",
               "payload_digest": "a" * 64, "ledger_id": "1783783039"}
    rec = prov.mint_signed(request, 900, 1700000000.777, "cli_tty")   # float now
    assert isinstance(rec["approved_at"], int) and rec["approved_at"] == 1700000000
    assert isinstance(rec["expires_at"], int) and rec["expires_at"] == 1700000900
    cb = sp._canonical_signing_bytes(rec)
    assert b'"approved_at":1700000000,' in cb
    assert b'"approved_at":1700000000.0' not in cb   # float repr 의존 0


def test_provider_jsonl_roundtrip_reverifies():
    """mint→json.dumps→json.loads→verify 왕복에서 서명 재검증 OK(비결정 0)."""
    prov = _provider("rt")
    request = {"request_id": "rid-RT", "protocol_version": "tae-1", "operation": "import_edges",
               "payload_digest": "b" * 64, "ledger_id": "1783783039"}
    rec = prov.mint_signed(request, 900, 1700000000.999, "cli_tty")
    reparsed = json.loads(json.dumps(rec, ensure_ascii=False))
    assert prov.verify_signed(reparsed).get("ok") is True
    # 동일 서명대상 재서명 결정성
    unsigned = {k: rec[k] for k in rec if k != "sig"}
    assert prov.sign_record(unsigned)["sig"] == rec["sig"]


def test_trusted_approval_mint_verify_roundtrip_signed(tmp_path, monkeypatch):
    """WF2 Core 왕복 — ta.mint_approval(int now)→approvals.jsonl→read_events 역파싱→verify_event OK."""
    home = _keychain_home(tmp_path, monkeypatch, seed="core-rt")
    request = {"request_id": "rid-CORE", "protocol_version": ta.PROTOCOL_VERSION,
               "operation": "import_edges", "payload_digest": "c" * 64, "ledger_id": "1783783039"}
    rec = ta.mint_approval(home, request, 900, 1700000000.5, channel="cli_tty")
    assert "sig" in rec and isinstance(rec["sig"], str)
    assert isinstance(rec["approved_at"], int)

    # 디스크에서 재파싱한 record 로 서명 재검증
    events = ta.read_events(home)
    assert len(events) == 1 and events[0].get("sig") == rec["sig"]

    v = ta.verify_event(home, "rid-CORE", "import_edges", "c" * 64, "1783783039", 1700000100,
                        protocol_version=ta.PROTOCOL_VERSION)
    assert v.get("ok") is True, v


def test_trusted_approval_signature_catches_label_wash(tmp_path, monkeypatch):
    """approver_channel 은 verify_event pre-check 밖 → L1 은 못 잡고 L2 서명만 잡는다.

    approvals.jsonl 의 approver_channel 을 손으로 세탁하면 pre-check(operation/payload/ledger/protocol)는
    통과하지만 서명 검증이 binding_mismatch:signature 로 거부한다(L2 실효 배선 단언)."""
    home = _keychain_home(tmp_path, monkeypatch, seed="wash")
    request = {"request_id": "rid-WASH", "protocol_version": ta.PROTOCOL_VERSION,
               "operation": "import_edges", "payload_digest": "d" * 64, "ledger_id": "1783783039"}
    ta.mint_approval(home, request, 900, 1700000000, channel="unverified_direct")

    # 라벨 세탁: approver_channel 을 cli_tty 로 위조(서명은 그대로) → 서명 검증만 이걸 잡는다.
    path = ta.event_store_path(home)
    with open(path, "r", encoding="utf-8") as f:
        rec = json.loads(f.read().strip())
    rec["approver_channel"] = "cli_tty"
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    v = ta.verify_event(home, "rid-WASH", "import_edges", "d" * 64, "1783783039", 1700000100,
                        protocol_version=ta.PROTOCOL_VERSION)
    assert v.get("ok") is False
    assert v.get("reason") == "binding_mismatch:signature", v


# ══════════════════════════════════════════════════════════════════════════════════
# 4) provider None / local_owner 무회귀 — byte-identical (sig 키 미append)
# ══════════════════════════════════════════════════════════════════════════════════
def test_signing_provider_for_none_and_local_owner(tmp_path):
    assert sp.signing_provider_for(str(tmp_path / "no-such")) is None
    home = str(tmp_path)
    _write_config(home, "local_owner")
    assert sp.signing_provider_for(home) is None
    _write_config(home, None)   # kind 없는 기존 config → local_owner 기본화 → None
    assert sp.signing_provider_for(home) is None


def test_provider_for_local_owner_no_signing(tmp_path):
    home = str(tmp_path)
    _write_config(home, "local_owner")
    prov = ta.provider_for(home)
    assert prov is not None and prov.kind == "local_owner"
    assert prov.kind not in sp.SIGNING_KINDS


@pytest.mark.parametrize("kind", ["local_owner", None])
def test_mint_local_owner_byte_identical_no_sig(tmp_path, kind):
    """local_owner/no-kind mint record 는 sig 키 미보유 — 디스크 라인에도 "sig" 문자열 0."""
    home = str(tmp_path)
    _write_config(home, kind)
    request = {"request_id": "rid-LO", "protocol_version": ta.PROTOCOL_VERSION,
               "operation": "import_edges", "payload_digest": "d" * 64, "ledger_id": "1783783039"}
    rec = ta.mint_approval(home, request, 900, 1700000000, channel="cli_tty")
    assert "sig" not in rec
    expected_keys = {"request_id", "protocol_version", "operation", "payload_digest",
                     "ledger_id", "approval_nonce", "approved_at", "expires_at",
                     "approver_channel", "record_type"}
    assert set(rec.keys()) == expected_keys
    raw = open(ta.event_store_path(home), "r", encoding="utf-8").read()
    assert '"sig"' not in raw


def test_verify_event_local_owner_skips_signature(tmp_path):
    """local_owner 는 verify_event 서명 분기 skip — 무서명 approve 를 정상 accept(오늘과 동일)."""
    home = str(tmp_path)
    _write_config(home, "local_owner")
    request = {"request_id": "rid-LOV", "protocol_version": ta.PROTOCOL_VERSION,
               "operation": "import_edges", "payload_digest": "e" * 64, "ledger_id": "1783783039"}
    ta.mint_approval(home, request, 900, 1700000000, channel="cli_tty")
    v = ta.verify_event(home, "rid-LOV", "import_edges", "e" * 64, "1783783039", 1700000100,
                        protocol_version=ta.PROTOCOL_VERSION)
    assert v.get("ok") is True, v


# ══════════════════════════════════════════════════════════════════════════════════
# 5) L0 fail-closed — backend 미가용이면 verify 항상 실패(무서명 accept 0)
# ══════════════════════════════════════════════════════════════════════════════════
def test_unavailable_backend_is_fail_closed(tmp_path):
    """kind=keychain + 미가용 backend → _UnavailableSigningProvider. verify 항상 False · mint raise."""
    home = str(tmp_path)
    _write_config(home, "keychain")
    up = sp.signing_provider_for(home, backend=kb._UnavailableKeychainBackend("test"))
    assert isinstance(up, sp._UnavailableSigningProvider)
    assert up.public_key is None
    assert up.verify_signed(_base_record()).get("ok") is False
    with pytest.raises(sp.SigningUnavailable):
        up.mint_signed({"request_id": "r", "protocol_version": "tae-1", "operation": "x",
                        "payload_digest": "a" * 64, "ledger_id": "1"}, 900, 1700000000, "cli_tty")


def test_unavailable_backend_verify_never_accepts_unsigned(tmp_path):
    """미가용 provider 는 무서명 record 든 서명 record 든 절대 True 를 내지 않는다(fail-open 0)."""
    home = str(tmp_path)
    _write_config(home, "keychain")
    up = sp.signing_provider_for(home, backend=kb._UnavailableKeychainBackend("test"))
    signed = _provider("fc").sign_record(_base_record())   # 유효 서명이라도
    assert up.verify_signed(signed).get("ok") is False
    assert up.verify_signed(_base_record()).get("ok") is False


def test_unavailable_backend_verify_event_fail_closed(tmp_path, monkeypatch):
    """kind=keychain 인데 backend 미가용이면 verify_event 는 binding_mismatch:signature(fail-closed).

    무서명 approve 가 디스크에 있어도(예: L1 시절 record) L2 config 하에선 accept 되지 않는다."""
    home = str(tmp_path)
    _write_config(home, "keychain")
    monkeypatch.setattr(sp, "default_keychain_backend",
                        lambda: kb._UnavailableKeychainBackend("forced"))
    # 무서명 approve 를 직접 append(L1 잔재 시뮬)
    ta.append_event(home, {
        "request_id": "rid-FC", "protocol_version": ta.PROTOCOL_VERSION, "operation": "import_edges",
        "payload_digest": "f" * 64, "ledger_id": "1783783039", "approval_nonce": "00" * 16,
        "approved_at": 1700000000, "expires_at": 1700000900,
        "approver_channel": "cli_tty", "record_type": "approve"})
    v = ta.verify_event(home, "rid-FC", "import_edges", "f" * 64, "1783783039", 1700000100,
                        protocol_version=ta.PROTOCOL_VERSION)
    assert v.get("ok") is False
    assert v.get("reason") == "binding_mismatch:signature", v


# ══════════════════════════════════════════════════════════════════════════════════
# 6) 격리 — 주입 seam · import-time 부작용 0 · 실 keychain 미접촉 · secret 위생
# ══════════════════════════════════════════════════════════════════════════════════
def test_backend_injection_seam():
    fake = _fake("seam")
    assert kb.get_backend(inject=fake) is fake        # 주입 시 그대로
    assert kb.get_backend() is not fake               # None → 실 플랫폼 백엔드(fake 아님)


def test_keychain_backend_import_time_no_os_binding():
    """keychain_backend top-level 에 ctypes/subprocess/shutil/secrets binding 0(신규모듈 게이트)."""
    modvars = set(vars(kb).keys())
    assert not ({"ctypes", "subprocess", "shutil", "secrets"} & modvars)
    assert {"hashlib", "os", "sys"} <= modvars


def test_signing_provider_import_time_stdlib_only():
    modvars = set(vars(sp).keys())
    for forbidden in ("keyring", "cryptography", "nacl"):
        assert forbidden not in modvars


def test_real_backend_readonly_not_the_fake():
    """inject 없는 실 backend 는 available()/peek(read-only)만 노출 — 이 호출로 항목 생성 0."""
    real = kb.get_backend()
    fake = _fake("cmp")
    assert real is not fake
    assert isinstance(real.available(), bool)
    # peek_key_present 는 read-only. 존재하지 않는 sentinel key 는 True 를 내지 않는다.
    sentinel = "binggupack.selftest.DO_NOT_CREATE.signing_test"
    assert real.peek_key_present(sentinel) in (False, None)


def test_fake_deterministic_and_pk_matches_sk():
    fake = _fake("det-fake")
    sk, pk = fake.load_or_create_signing_key("kid-A")
    assert isinstance(sk, (bytes, bytearray)) and len(sk) == 32
    assert bytes(pk) == sp.ed25519_publickey(bytes(sk))
    sk2, pk2 = fake.load_or_create_signing_key("kid-A")
    assert bytes(sk2) == bytes(sk) and bytes(pk2) == bytes(pk)   # idempotent
    sk3, _ = _fake("det-fake").load_or_create_signing_key("kid-A")
    assert bytes(sk3) == bytes(sk)   # 같은 seed → 결정적(인스턴스 무관)


@pytest.mark.parametrize("describe", [sp.describe_secret, kb.describe_secret])
def test_describe_secret_no_plaintext_leak(describe):
    """sk 원문 미노출 — sha256 hash8 + 길이만. 평문/hex 가 반환 어디에도 없다(영구금지 18)."""
    sk = bytes(range(32))
    d = describe(sk)
    assert set(d.keys()) == {"sha256_hash8", "length"}
    assert len(d["sha256_hash8"]) == 8 and d["length"] == 32
    assert sk.hex() not in str(d) and str(sk) not in str(d)


def test_selftests_gate_go():
    """두 모듈 --selftest 가 GATE=GO(내부 KAT 배터리 전량 통과)."""
    gate_sp, p_sp, t_sp = sp._selftest()
    gate_kb, p_kb, t_kb = kb._selftest()
    assert gate_sp == "GO", "signing_provider selftest %d/%d" % (p_sp, t_sp)
    assert gate_kb == "GO", "keychain_backend selftest %d/%d" % (p_kb, t_kb)
