#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu_protected_writer_attack_demo.py

defensive security · attack-model demonstration · P1-B Track B · 운영 store 미접촉.

정본 설계: docs/BINGGUPACK_PROTECTED_WRITER_RFC.md (§1.2 위조 절차 · §3 detached signer · §6 프로토타입).

무엇을 보이는가 (전부 temp 격리 home · 실 ~/.binggupack 미접촉 sentinel 확인):
  1. ATTACK L1(현행): FS/Shell 병재 호스트의 모델은 approval EVENT store(평문 approvals.jsonl)에
     approve 레코드를 **직접 위조 append** 하고 자가 consume 할 수 있다 → verify_event 가 위조를
     진짜로 신뢰(BYPASS 성립). 이는 P1-A/Track A 의 exact binding 이 "store 가 진실"에 의존하기
     때문이다. (boundary selftest 가 정당 테스트로 쓰는 append_event 가 곧 위조 벡터.)
  2. DEFENSE(detached signer 시뮬): 서명키를 모델 tool surface 밖(vault = keychain L2 / 별도 프로세스
     L3 / 디바이스 L4 표현)에 두면, 모델은 store 파일에 평문을 append 할 수는 있어도 **유효 서명을
     만들 수 없어** hardened verify 가 위조를 거부(fail-closed) → owner 서명 레코드만 통과.

정직 경계(과장 0 · P1-A §6 계승):
  · 이 데모의 verify 는 vault 키를 읽어 검증한다 = "검증도 보호 도메인 안(L3 helper)" 또는 "keychain
    접근(L2, 코드실행 없을 때)". 실 구현 원칙은 **비대칭 서명(Ed25519)** — consume 은 공개키만으로
    검증(sk 미노출). HMAC 은 여기선 stdlib-only 대칭 stand-in일 뿐(RFC §3.2).
  · ★가정: 공격자(모델 도구)가 vault sk 를 **read 불가**. 이 가정이 깨지면(모델이 셸로 vault 를 읽으면)
    L2 는 §6 "같은 머신 키 = 보안 연극"으로 환원된다 — owner dev 머신(셸 병재)에서 실질 하드의 최소선은
    L3(별도 사용자/프로세스), 완전은 L4(별도 디바이스). 이 데모는 "L2 시뮬이 하드"라고 주장하지 않는다.

CLI: python scripts/binggu_protected_writer_attack_demo.py --selftest
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import tempfile
import time
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))


# ── 모델 tool surface 시뮬 (allow-root 밖 read/write 거부) ────────────────────────
class ToolSurface:
    """모델에게 준 FS/Bash 도구의 접근 경계 시뮬. allow_roots 밖은 read/write 거부.

    실제 위협에서 모델은 binggupack MCP + Filesystem/Bash MCP 를 병재로 갖는다. FS MCP 가 allow-root
    로 제한돼도(예: ~/.binggupack) 그 안의 approval store 는 write 가능 = L1 우회. vault 를 allow-root
    밖에 두면(L(b))·keychain/별도 프로세스(L2/L3) FS 도구는 못 건드린다. 단 '셸'까지 있으면 allow-root
    무의미(§4 정직 한계) — 이 시뮬은 'FS-only 도구'의 경계만 표현한다."""

    def __init__(self, allow_roots):
        self.allow = [os.path.abspath(r) for r in allow_roots]

    def _allowed(self, path):
        ap = os.path.abspath(path)
        return any(ap == r or ap.startswith(r + os.sep) for r in self.allow)

    def write_text(self, path, data, mode="a"):
        if not self._allowed(path):
            raise PermissionError("tool-surface DENY write outside allow-root: %s" % os.path.basename(path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, encoding="utf-8") as f:
            f.write(data)

    def read_text(self, path):
        if not self._allowed(path):
            raise PermissionError("tool-surface DENY read outside allow-root: %s" % os.path.basename(path))
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


# ── detached signer 시뮬 (키는 vault=보호 도메인 안) ──────────────────────────────
def _canon_record(record):
    """서명 대상 = sig 제외 canonical(필드 한 개 변조 시 서명 불일치)."""
    body = {k: v for k, v in record.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def owner_sign(vault_key, record):
    """보호 도메인(owner) 안에서만 호출 가능한 서명. HMAC 은 stdlib stand-in(실구현=Ed25519·비대칭)."""
    mac = hmac.new(vault_key, _canon_record(record).encode("utf-8"), hashlib.sha256).hexdigest()
    out = dict(record)
    out["sig"] = mac
    return out


def verify_signed(vault_key, record):
    """hardened verify: 유효 서명 없는 레코드 거부(fail-closed). 실구현은 공개키로 검증(sk 불요)."""
    sig = record.get("sig")
    if not sig:
        return False
    expect = hmac.new(vault_key, _canon_record(record).encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)


def run():
    from binggupack.safety import trusted_approval as ta

    fails = []

    def ck(name, cond):
        print("  [%s] %s" % ("OK" if cond else "X", name))
        if not cond:
            fails.append(name)

    # ── 운영 ledger sentinel — 실 ~/.binggupack 미접촉 확인(전 구간 mtime 불변) ──────
    real_home = os.path.join(os.path.expanduser("~"), ".binggupack")
    sentinel = {}
    for fn in ("ledger.sqlite", "approvals.jsonl", "trusted_approval.json"):
        p = os.path.join(real_home, fn)
        sentinel[p] = os.path.getmtime(p) if os.path.exists(p) else None

    home = tempfile.mkdtemp(prefix="bgp_pwriter_home_")     # 모델 tool surface(=~/.binggupack 시뮬)
    vault = tempfile.mkdtemp(prefix="bgp_pwriter_vault_")   # 보호 도메인(keychain/별도 프로세스/디바이스 시뮬)
    try:
        now = time.time()
        # 승인 대상 mutation = 모델이 MCP 로 만든 PENDING request(동일 intent = 동일 request_id).
        op = "deprecate"
        payload = {"index": 1, "id8": "abc1234f", "reason": "오판이라 기각"}
        ledger_id = "demo-ledger-" + uuid.uuid4().hex[:12]
        payload_digest = ta.canonical_payload_digest(op, payload)
        request_id = ta.compute_request_id(op, payload_digest, ledger_id)
        request = {"request_id": request_id, "protocol_version": ta.PROTOCOL_VERSION,
                   "operation": op, "payload_digest": payload_digest, "ledger_id": ledger_id}

        # 공격자 = home(모델 tool surface)만 접근. vault 는 밖(read/write 거부).
        attacker = ToolSurface([home])

        # ══ 1) ATTACK L1 — 평문 store 위조 성립(현행) ═══════════════════════════════
        print("\n[1] ATTACK L1 (현행 · 평문 approvals.jsonl · FS 병재 호스트)")
        # 모델(FS 도구)이 owner TTY 없이 approve 레코드를 직접 위조 append.
        forged = dict(request)
        forged.update({"approval_nonce": secrets.token_hex(16), "approved_at": now,
                       "expires_at": now + 900, "record_type": "approve"})
        # append_event 는 home 안(approvals.jsonl)에 쓴다 → 공격자 tool-surface 로 도달 가능.
        store_path = ta.event_store_path(home)
        ck("공격자가 approval store(평문·home 내)에 write 가능",
           attacker._allowed(store_path))
        ta.append_event(home, forged)   # = 모델이 Bash/FS MCP 로 한 줄 append 한 것
        res = ta.verify_event(home, request_id, op, payload_digest, ledger_id, now)
        ck("L1: verify_event 가 위조 approve 를 신뢰(ok=True) → BYPASS 성립(현행 한계 실증)",
           res.get("ok") is True)
        # → 이후 reserve/finalize → executed_write=True. owner 관여 0. 이것이 Track B 가 막는 위협.

        # ══ 2) DEFENSE — detached signer(키=vault=보호 도메인) ══════════════════════
        print("\n[2] DEFENSE (detached signer · 서명키 vault 안 · 모델 tool surface 밖)")
        vault_key_path = os.path.join(vault, "signer.key")
        with open(vault_key_path, "wb") as f:
            f.write(secrets.token_bytes(32))     # owner 도메인만 접근(공격자 read 불가)
        with open(vault_key_path, "rb") as f:
            vault_key = f.read()

        # 서명 store 파일은 home 안이라도(공격자 append 가능) 무방 — 보안은 '키 위치'에서 온다(§3.1).
        signed_store = os.path.join(home, "approvals_signed.jsonl")

        # owner(보호 도메인): 서명된 approve 레코드 발행.
        owner_record = dict(request)
        owner_record.update({"approval_nonce": secrets.token_hex(16), "approved_at": now,
                             "expires_at": now + 900, "record_type": "approve"})
        owner_signed = owner_sign(vault_key, owner_record)
        with open(signed_store, "a", encoding="utf-8") as f:
            f.write(json.dumps(owner_signed, ensure_ascii=False) + "\n")
        ck("owner 서명 레코드는 hardened verify 통과",
           verify_signed(vault_key, owner_signed) is True)

        # 공격자 시도 A: 서명 store 에 평문 forge append(파일은 home 내라 write 가능).
        attacker_forge = dict(request)
        attacker_forge.update({"approval_nonce": secrets.token_hex(16), "approved_at": now,
                               "expires_at": now + 900, "record_type": "approve"})
        attacker.write_text(signed_store, json.dumps(attacker_forge, ensure_ascii=False) + "\n")
        ck("DEFENSE: 서명 없는 공격자 forge → verify_signed 거부(fail-closed)",
           verify_signed(vault_key, attacker_forge) is False)

        # 공격자 시도 B: 유효 서명을 만들려면 vault 키가 필요 → tool-surface 밖 read 거부.
        vault_read_blocked = False
        try:
            attacker.read_text(vault_key_path)
        except PermissionError:
            vault_read_blocked = True
        ck("DEFENSE: 공격자가 vault 서명키 read 불가(→ 유효 서명 위조 불가)",
           vault_read_blocked)

        # 공격자 시도 C: 아무 키로나 서명 위조 → verify 불일치.
        wrong_key = secrets.token_bytes(32)
        attacker_wrong = owner_sign(wrong_key, attacker_forge)
        ck("DEFENSE: 다른 키로 서명한 forge → verify_signed 거부",
           verify_signed(vault_key, attacker_wrong) is False)

        # 서명 변조: owner 레코드 payload 한 글자 바꾸면 서명 불일치(서명이 전체 canonical 바인딩).
        tampered = dict(owner_signed)
        tampered["payload_digest"] = tampered["payload_digest"][:-1] + ("0" if tampered["payload_digest"][-1] != "0" else "1")
        ck("DEFENSE: owner 서명 후 필드 변조 → verify_signed 거부(바인딩 무결성)",
           verify_signed(vault_key, tampered) is False)

        # ══ 3) protected writer(store 위치 격리 · L(b)/L3) 시뮬 ═════════════════════
        print("\n[3] PROTECTED WRITER (store 를 tool surface 밖 도메인에 · L(b)/L3)")
        protected_path = os.path.join(vault, "approvals_protected.jsonl")
        write_blocked = False
        try:
            attacker.write_text(protected_path, "forge\n")
        except PermissionError:
            write_blocked = True
        ck("공격자가 vault(도구 표면 밖) 소유 store 에 write 불가 → 평문 위조 append 자체가 봉쇄",
           write_blocked)
        print("      (정직: 위 봉쇄는 '모델에 셸이 없다'는 전제에서만 하드 — 셸 병재면 무력 · §4/§8-R-B2)")

        # ── 정직 캡션(반드시 출력) ─────────────────────────────────────────────────
        print("\n[정직 경계]")
        print("  · L1(현행)은 FS/Shell 병재 호스트에서 하드 통제 아님 — 위 [1]이 위조 성립을 실증.")
        print("  · [2] DEFENSE 는 vault sk 가 모델 도구로 read 불가라는 가정 위에서만 성립.")
        print("    가정 붕괴(모델이 셸로 vault read) → L2 는 '같은 머신 키=보안 연극'으로 환원(§6).")
        print("  · owner dev 머신(셸 병재) 실질 하드 최소선 = L3(별도 프로세스/사용자), 완전 = L4(별도 디바이스).")
        print("  · 이 데모는 '이제 안전하다'를 주장하지 않는다 — 각 등급의 미차단 공격은 RFC §4 참조.")

    finally:
        # ── 운영 홈 sentinel 재확인(미접촉) ─────────────────────────────────────────
        untouched = True
        for p, mt in sentinel.items():
            cur = os.path.getmtime(p) if os.path.exists(p) else None
            if cur != mt:
                untouched = False
        ck("운영 ~/.binggupack 미접촉(sentinel mtime 불변)", untouched)
        # temp 정리
        for d in (home, vault):
            try:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

    gate = "GO" if not fails else "BLOCK"
    print("\n" + "=" * 64)
    print("PROTECTED-WRITER ATTACK-DEMO GATE=%s (%d fail)" % (gate, len(fails)))
    if fails:
        print("  실패:", ", ".join(fails))
    return 0 if gate == "GO" else 1


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        print("=" * 64)
        print("BingguPack Protected Writer — attack-model demonstration (defensive)")
        print("P1-B Track B · docs/BINGGUPACK_PROTECTED_WRITER_RFC.md · 운영 store 미접촉")
        print("=" * 64)
        sys.exit(run())
    print("usage: python scripts/binggu_protected_writer_attack_demo.py --selftest")
    sys.exit(2)


if __name__ == "__main__":
    main()
