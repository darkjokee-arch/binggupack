#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""binggu_protected_writer_attack_demo.py

defensive security · attack-model demonstration · P1-B Track B(PW-Wire) · 운영 store 미접촉.

정본 설계: docs/BINGGUPACK_PROTECTED_WRITER_RFC.md (§1.2 위조 절차 · §3 detached signer · §6 프로토타입).
서명 계층 정본 구현: binggupack/safety/signing_provider.py(Ed25519·PW-Core1) + keychain_backend.py(PW-Core2).

무엇을 보이는가 (전부 temp 격리 home · 실 ~/.binggupack·실 OS keychain 미접촉 sentinel 확인):
  1. ATTACK L1(kind無 / local_owner): FS/Shell 병재 호스트의 모델은 approval EVENT store(평문
     approvals.jsonl)에 approve 레코드를 **직접 위조 append** 하고 자가 consume 할 수 있다 →
     verify_event 가 위조를 진짜로 신뢰(ok=True · BYPASS 성립). 서명 계층이 붙지 않는 kind 라
     "store 가 진실"에 의존하기 때문이다.
  2. DEFENSE L2(keychain 서명): kind:keychain 이면 verify_event 가 approve EVENT 의 Ed25519 서명을
     공개키로 검증한다. 모델은 store 파일에 평문을 append 할 수는 있어도 **유효 서명을 만들 수 없어**
     (sk 는 keychain 안·모델 tool surface 밖) verify_event 가 위조를 거부(ok=False ·
     binding_mismatch:signature). 단 이것은 **FS-only 도구**에 한한 차단이다(아래 정직 라벨).

★ 정직 라벨 (절대 준수 — signing_provider.py / keychain_backend.py / RFC / SECURITY / PR 과 동일 3문장):
  1) L2(keychain 서명)는 owner 머신(Shell/FS MCP 병재)에서 위조를 못 막는다 — 같은 uid 셸이 keychain
     에서 sk 를 로드해 스스로 서명하면 "같은 머신 키 = 보안 연극"이 성립한다.
  2) config 의 kind 는 모델-writable 평문이라, kind:local_owner 한 줄로 서명 검증을 통째 skip 시킬 수
     있다(R-B6 · P1-B2 · out-of-scope). 즉 로컬 config 자체가 신뢰 경계 밖이다.
  3) L2 의 실질 값은 hosted/locked 배포(모델이 셸/keychain/config 를 못 건드리는 환경)에서만 나온다.
  → 이 데모/문서 어디에도 "로컬에서 위조 차단" · "로컬 보안 강화" 라고 쓰지 않는다. 셸병재 = 연극 환원.

격리(운영 오염 0):
  · keychain backend 는 in-memory fake(InMemoryKeychainBackend)를 signing_provider 경로에 주입한다.
    실 OS keychain·실 ~/.binggupack 은 접촉하지 않는다. 시작/종료에 운영홈 ledger + 실 keychain 항목
    sentinel(mtime/size·present) 불변을 단언한다(fake 미주입이면 실 keychain 을 건드려 FAIL 하도록 설계).
  · secret 위생: sk/키 원문을 print/log 하지 않는다(describe_secret = sha256 hash8 + 길이만).

CLI: python scripts/binggu_protected_writer_attack_demo.py --selftest
"""
from __future__ import annotations

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
    로 제한돼도(예: ~/.binggupack) 그 안의 approval store 는 write 가능 = L1 우회. keychain(별도 스토어)
    은 파일이 아니라 FS 도구로 못 읽는다(L2 = FS-only 차단). 단 '셸'까지 있으면 keychain 도 읽힌다
    (§4/§6 정직 한계) — 이 시뮬은 'FS-only 도구'의 경계만 표현한다."""

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


def _forge_unsigned_approve(request, now):
    """모델(FS 도구)이 owner TTY 없이 직접 만드는 무서명 approve 레코드(= 위조 벡터)."""
    forged = dict(request)
    forged.update({"approval_nonce": secrets.token_hex(16), "approved_at": int(now),
                   "expires_at": int(now) + 900, "approver_channel": "cli_tty",
                   "record_type": "approve"})
    return forged


def _write_keychain_config(home, kind):
    """owner-controlled trusted_approval.json 작성(temp home · 운영 미접촉). kind 로 L1/L2 분기."""
    from binggupack.safety import trusted_approval as ta
    with open(ta.config_path(home), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "kind": kind, "ttl_seconds": 900, "pending_cap": 64}, f)


def run():
    from binggupack.safety import keychain_backend as kb
    from binggupack.safety import signing_provider as sp
    from binggupack.safety import trusted_approval as ta

    fails = []

    def ck(name, cond):
        print("  [%s] %s" % ("OK" if cond else "X", name))
        if not cond:
            fails.append(name)

    # ── 운영 sentinel — 실 ~/.binggupack ledger + 실 OS keychain 항목(전 구간 불변) ──────
    real_home = os.path.join(os.path.expanduser("~"), ".binggupack")
    sentinel = {}
    for fn in ("ledger.sqlite", "capture_buffer.sqlite", "approvals.jsonl", "trusted_approval.json"):
        p = os.path.join(real_home, fn)
        sentinel[p] = os.path.getmtime(p) if os.path.exists(p) else None
    kc_key_id = sp.KeychainProvider._DEFAULT_KEY_ID
    kc_before = kb._real_keychain_sentinel(kc_key_id)   # 읽기 전용(write 0). 실 keychain 우리 항목 present

    # 공통: 승인 대상 mutation = 모델이 MCP 로 만든 PENDING request(동일 intent = 동일 request_id).
    now = time.time()
    op = "deprecate"
    payload = {"index": 1, "id8": "abc1234f", "reason": "오판이라 기각"}
    ledger_id = "demo-ledger-" + uuid.uuid4().hex[:12]
    payload_digest = ta.canonical_payload_digest(op, payload)
    request_id = ta.compute_request_id(op, payload_digest, ledger_id)
    request = {"request_id": request_id, "protocol_version": ta.PROTOCOL_VERSION,
               "operation": op, "payload_digest": payload_digest, "ledger_id": ledger_id}

    home_l1 = tempfile.mkdtemp(prefix="bgp_pw_l1_")     # kind無 / local_owner (서명 계층 없음)
    home_l2 = tempfile.mkdtemp(prefix="bgp_pw_l2_")     # kind:keychain (Ed25519 서명 계층)

    # ── in-memory fake keychain backend 를 signing_provider 경로에 주입 ────────────────
    #   provider_for(home) → signing_provider_for(home) → default_keychain_backend() 를 fake 로 치환.
    #   실 OS keychain·실 ~/.binggupack 미접촉(주입 seam 기본값 = fake). 종료 시 원복.
    fake = sp.InMemoryKeychainBackend(seed="attack-demo-fixed-seed")
    _orig_default_backend = sp.default_keychain_backend
    sp.default_keychain_backend = lambda: fake

    try:
        # ══ 1) ATTACK L1 — 평문 store 위조 성립 (kind無 / local_owner · 서명 계층 없음) ═══
        print("\n[1] ATTACK L1 (kind無/local_owner · 평문 approvals.jsonl · FS 병재 호스트)")
        _write_keychain_config(home_l1, "local_owner")   # 서명 계층 미개입 kind
        attacker_l1 = ToolSurface([home_l1])
        store_l1 = ta.event_store_path(home_l1)
        ck("공격자가 approval store(평문·home 내)에 write 가능", attacker_l1._allowed(store_l1))
        forged_l1 = _forge_unsigned_approve(request, now)
        ta.append_event(home_l1, forged_l1)              # = 모델이 Bash/FS MCP 로 한 줄 append
        res_l1 = ta.verify_event(home_l1, request_id, op, payload_digest, ledger_id, now)
        ck("L1: verify_event 가 무서명 위조 approve 를 신뢰(ok=True) → BYPASS 성립(kind無 한계 실증)",
           res_l1.get("ok") is True)
        # → 이후 reserve/finalize → executed_write=True. owner 관여 0. 이것이 Track B 가 겨냥하는 위협.

        # ══ 2) DEFENSE L2 — keychain Ed25519 서명 (verify_event 실배선 · FS-only 차단) ═══
        print("\n[2] DEFENSE L2 (kind:keychain · Ed25519 서명 · verify_event 실검증)")
        _write_keychain_config(home_l2, "keychain")
        prov = ta.provider_for(home_l2)
        ck("kind:keychain → provider = KeychainProvider(Ed25519 · fake backend 주입)",
           isinstance(prov, sp.KeychainProvider) and prov.kind == "keychain")
        # 공개키 핀(hash8+길이)만 — sk 평문 0. describe_secret 로 위생 확인.
        pk_desc = sp.describe_secret(prov.public_key)
        print("      keychain pk(pin): sha256=%s len=%d (sk 평문 미출력)"
              % (pk_desc["sha256_hash8"], pk_desc["length"]))

        # (2a) owner 서명 mint(cli_tty) → verify_event ok=True (서명 검증 통과).
        owner_rec = ta.mint_approval(home_l2, request, 900, now, channel="cli_tty")
        ck("owner mint 레코드에 Ed25519 sig 부여됨(64B hex)",
           isinstance(owner_rec.get("sig"), str) and len(bytes.fromhex(owner_rec["sig"])) == 64)
        res_l2_owner = ta.verify_event(home_l2, request_id, op, payload_digest, ledger_id, now)
        ck("L2: owner 서명 approve → verify_event ok=True(서명 검증 통과)",
           res_l2_owner.get("ok") is True)

        # (2b) 공격자 무서명 forge append → verify_event 거부(binding_mismatch:signature).
        #      별도 request(B)로 — owner mint 없이 FS-only append 만으로는 유효 서명이 안 나온다.
        payload_b = {"index": 2, "id8": "beef5678", "reason": "무서명 위조 시도"}
        ledger_b = "demo-ledger-" + uuid.uuid4().hex[:12]
        pdig_b = ta.canonical_payload_digest(op, payload_b)
        rid_b = ta.compute_request_id(op, pdig_b, ledger_b)
        req_b = {"request_id": rid_b, "protocol_version": ta.PROTOCOL_VERSION,
                 "operation": op, "payload_digest": pdig_b, "ledger_id": ledger_b}
        attacker_l2 = ToolSurface([home_l2])
        forged_l2 = _forge_unsigned_approve(req_b, now)   # sig 키 없음
        # 공격자는 approvals.jsonl(home 내)에 평문 append 가능 — write 자체는 막지 못한다(FS 도구 경계 안).
        attacker_l2.write_text(ta.event_store_path(home_l2),
                               json.dumps(forged_l2, ensure_ascii=False) + "\n")
        ck("공격자가 서명 store 에 무서명 forge 를 append(파일 write 자체는 성립 = FS-only 경계 안)",
           ta.find_approve(home_l2, rid_b) is not None)
        res_l2_forge = ta.verify_event(home_l2, rid_b, op, pdig_b, ledger_b, now)
        ck("L2: 무서명 forge → verify_event ok=False · reason=binding_mismatch:signature(FS-only 차단)",
           res_l2_forge.get("ok") is False
           and res_l2_forge.get("reason") == "binding_mismatch:signature")

        # (2c) 공격자 서명 변조: owner 서명 레코드의 필드 1개를 바꾸면 서명 불일치(전체 바인딩).
        tampered = dict(owner_rec)
        tampered["payload_digest"] = (tampered["payload_digest"][:-1]
                                      + ("0" if tampered["payload_digest"][-1] != "0" else "1"))
        vt = prov.verify_signed(tampered)
        ck("L2: owner 서명 후 필드 변조 → verify_signed 거부(binding_mismatch:signature)",
           vt.get("ok") is False and vt.get("reason") == "binding_mismatch:signature")

        # (2d) 공격자 임의 키 서명: 다른 keychain(다른 sk)로 서명해도 owner pk 검증 불일치.
        other_backend = sp.InMemoryKeychainBackend(seed="attacker-other-key")
        other_prov = sp.KeychainProvider(home_l2, {"ttl_seconds": 900, "pending_cap": 64},
                                         backend=other_backend)
        wrong_signed = other_prov.sign_record(_forge_unsigned_approve(req_b, now))
        # owner provider(정본 pk)로 검증 → 다른 키 서명이므로 거부.
        vw = prov.verify_signed(wrong_signed)
        ck("L2: 다른 키로 서명한 forge → owner pk 검증 거부(binding_mismatch:signature)",
           vw.get("ok") is False and vw.get("reason") == "binding_mismatch:signature")

        # ══ 3) 정직 라벨: L2 는 셸병재 owner 머신에서 하드 통제가 아니다 ══════════════════
        print("\n[3] HONEST LABEL (L2 = FS-only 차단 · 셸병재 = 연극 환원)")
        # 같은 uid 셸은 같은 keychain backend(fake=시뮬)로 sk 를 로드해 스스로 유효 서명할 수 있다.
        shell_forge = prov.sign_record(_forge_unsigned_approve(req_b, now))   # 셸이 keychain 접근 = 유효 서명
        vs = prov.verify_signed(shell_forge)
        ck("정직: 같은 머신 셸이 keychain sk 로드→서명하면 verify 통과(=위조 성립 · L2 무력)",
           vs.get("ok") is True)
        # config kind 는 모델-writable 평문 — kind:local_owner 한 줄로 서명 검증이 통째 skip 된다.
        _write_keychain_config(home_l2, "local_owner")
        res_skip = ta.verify_event(home_l2, rid_b, op, pdig_b, ledger_b, now)  # 무서명 forge 가 이제 통과
        ck("정직: config kind:local_owner 로 바꾸면 무서명 forge 가 verify_event ok=True(서명 skip)",
           res_skip.get("ok") is True)
        _write_keychain_config(home_l2, "keychain")   # 원복(sentinel 영향 없음 · temp home)

        # ── 정직 캡션(반드시 출력) ─────────────────────────────────────────────────
        print("\n[정직 경계]")
        print("  · [1] L1(kind無)은 FS/Shell 병재 호스트에서 하드 통제 아님 — 무서명 위조 append 성립.")
        print("  · [2] L2(keychain)는 **FS-only 도구**의 무서명 forge 만 차단(verify_event 서명 검증).")
        print("    같은 uid 셸이 keychain 에서 sk 로드→서명하면 verify 통과 = '같은 머신 키=보안 연극'([3]).")
        print("  · config kind 는 모델-writable 평문 — kind:local_owner 한 줄로 서명 검증 통째 skip([3]).")
        print("  · L2 실질 값은 hosted/locked 배포(모델이 셸/keychain/config 미접촉)에서만. 로컬 위조 차단 아님.")
        print("  · 이 데모는 '이제 안전하다'를 주장하지 않는다 — 각 등급 미차단 공격은 RFC §4 참조.")

    finally:
        sp.default_keychain_backend = _orig_default_backend   # 주입 원복

        # ── 운영 sentinel 재확인(미접촉) ────────────────────────────────────────────
        untouched = True
        for p, mt in sentinel.items():
            cur = os.path.getmtime(p) if os.path.exists(p) else None
            if cur != mt:
                untouched = False
        ck("운영 ~/.binggupack ledger 미접촉(sentinel mtime 불변)", untouched)
        kc_after = kb._real_keychain_sentinel(kc_key_id)
        # 격리 증명 = 실 keychain 항목 상태 불변(available/present 동일). 기본 key_id 는 실 owner
        # 사용으로 이미 present 일 수 있으므로 '절대 부재'가 아니라 '불변'이 정확한 단언이다.
        ck("실 OS keychain 우리 항목 미접촉(available/present 불변 · fake 주입으로 실 keychain write 0)",
           kc_before == kc_after)

        for d in (home_l1, home_l2):
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
        print("P1-B Track B(PW-Wire) · Ed25519 keychain · docs/BINGGUPACK_PROTECTED_WRITER_RFC.md")
        print("=" * 64)
        sys.exit(run())
    print("usage: python scripts/binggu_protected_writer_attack_demo.py --selftest")
    sys.exit(2)


if __name__ == "__main__":
    main()
