#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hag_commit_reveal.py — commit-reveal 봉인 (hybrid_agi)

목적
  AI 제안(proposal)을 사람 답변(human_answer) 확정 전에 미리 엿보지 못하게
  봉인(seal)하고, 사람이 자기 답을 먼저 commit 한 뒤에만 제안을 reveal 한다.
  prior-peek(사전 엿보기) 차단 + nonce 위조 차단 + 재도출 동일성 보장.

운영 안전 (영구금지 준수)
  - 운영 ledger(~/.binggupack/ledger.sqlite·capture_buffer.sqlite) 미접촉(읽기도 X).
  - 운영 store write 0. 모든 상태는 프로세스 인메모리(CommitRevealVault).
  - actor != 'human' 은 commit_answer 에서 BLOCK (allowlist default-deny).
  - L0(사람 원문) 불변. AI 제안은 봉인된 휘발 상태일 뿐, 영구 노드/엣지 직접 못 씀.
  - 결정론적: 실시간 시각/난수 미사용. nonce·seal_ts·commit_ts 는 호출자 주입.

핵심 함수
  seal_proposal(text, nonce, seal_ts)
      -> {seal=sha256(text+nonce), seal_ts, sealed=True}
      (nonce/text 는 vault 내부 잠금보관, 반환에는 평문 미포함)
  commit_answer(qid, human_answer, commit_ts, actor='human')
      -> {answer_hash, commit_ts, committed=True}  (한 qid 1회, 수정불가)
  reveal_proposal(seal, nonce)
      -> commit 이후에만 허용. seal/nonce 검증 후 원문 공개·대조.
      불변식: commit 전 reveal BLOCK / nonce 불일치 BLOCK.

CLI
  python hag_commit_reveal.py --selftest   -> "GATE: GO" | "GATE: STOP"
"""
from __future__ import annotations

import hashlib
import hmac
import sys
from typing import Dict, Optional


def _normalize(s: str) -> str:
    """copy 탐지용 정규화 — 공백 collapse(유니코드 공백 포함) + strip + 소문자.
    H2-2: 공백/대소문자 편집 회피만 차단. **한계(정직)**: 구두점(마침표 등)·단어 치환·의미 유사는
    못 잡음(정규화 방식 한계). 완전 차단은 편집거리/의미유사가 필요 — MVP 범위 밖, 사람 도장이 최종 게이트."""
    return " ".join((s or "").split()).strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# 예외 — 불변식 위반은 BLOCK 으로 신호
# ─────────────────────────────────────────────────────────────────────────────
class CommitRevealBlock(Exception):
    """불변식 위반(prior-peek·nonce 위조·미커밋·actor 차단)."""


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def compute_seal(text: str, nonce: str) -> str:
    """seal = sha256(text + nonce). 순수 함수(재도출 동일)."""
    if not isinstance(text, str) or not isinstance(nonce, str):
        raise CommitRevealBlock("text/nonce 는 문자열이어야 함")
    if nonce == "":
        raise CommitRevealBlock("nonce 비어있음 — 봉인 불가")
    return _sha256_hex(text + nonce)


def compute_answer_hash(human_answer: str) -> str:
    """answer_hash = sha256(human_answer). 순수 함수."""
    if not isinstance(human_answer, str):
        raise CommitRevealBlock("human_answer 는 문자열이어야 함")
    return _sha256_hex(human_answer)


class CommitRevealVault:
    """
    commit-reveal 상태 보관소 (프로세스 인메모리 전용).
    운영 store/ledger 절대 미접촉. 영구 저장 없음(TTL 휘발 제안).
    """

    def __init__(self, secret: str) -> None:
        # secret = attestation HMAC 키(프로세스 주입·결정론 selftest 위해 주입형). 평문 노출 금지.
        if not secret or not isinstance(secret, str):
            raise CommitRevealBlock("vault secret 필수 — attestation 위조 방지 키(H2-1)")
        self._secret = secret.encode("utf-8")
        # seal -> {text, nonce, seal_ts, qid}  (잠금보관: 평문은 reveal 전까지 비공개)
        self._sealed: Dict[str, Dict[str, str]] = {}
        # qid -> {answer_hash, norm_hash, commit_ts, actor}  (수정불가)
        self._commits: Dict[str, Dict[str, str]] = {}

    # ── seal ────────────────────────────────────────────────────────────────
    def seal_proposal(self, text: str, nonce: str, seal_ts: int, qid: str) -> Dict[str, object]:
        """
        AI 제안 봉인. seal 만 공개, text/nonce 는 잠금보관.
        반환에 평문 미포함(prior-peek 방지).

        H1 fail-closed: seal 은 답변 질문 qid 에 **바인딩**된다(필수). reveal 시 그 qid 의
        commit 여부를 vault 가 내부적으로 강제 검사하므로, 호출자가 인자를 빠뜨려
        prior-peek 을 여는 우회가 구조적으로 불가능.
        """
        if not isinstance(seal_ts, int):
            raise CommitRevealBlock("seal_ts 는 정수 주입값이어야 함(실시간 시각 금지)")
        if not isinstance(qid, str) or qid == "":
            raise CommitRevealBlock("qid 필수 — seal 은 답변 질문에 바인딩되어야 함(fail-closed)")
        seal = compute_seal(text, nonce)
        # 동일 seal 재봉인 시: 동일 text/nonce/qid 면 멱등, 다르면 충돌 BLOCK
        if seal in self._sealed:
            prev = self._sealed[seal]
            if prev["text"] != text or prev["nonce"] != nonce or prev["qid"] != qid:
                raise CommitRevealBlock("seal 충돌 — 동일 seal 에 다른 내용/qid")
        else:
            self._sealed[seal] = {"text": text, "nonce": nonce,
                                  "seal_ts": str(seal_ts), "qid": qid}
        return {"seal": seal, "seal_ts": seal_ts, "qid": qid, "sealed": True}

    # ── commit ───────────────────────────────────────────────────────────────
    def commit_answer(
        self,
        qid: str,
        human_answer: str,
        commit_ts: int,
        actor: str = "human",
    ) -> Dict[str, object]:
        """
        사람 답변 확정(수정불가). actor != 'human' 은 BLOCK(allowlist default-deny).
        한 qid 에 1회만 — 재커밋 시도는 BLOCK.
        """
        if actor != "human":
            raise CommitRevealBlock(f"actor={actor!r} BLOCK — 사람만 commit 가능")
        if not isinstance(qid, str) or qid == "":
            raise CommitRevealBlock("qid 비어있음")
        if not isinstance(commit_ts, int):
            raise CommitRevealBlock("commit_ts 는 정수 주입값이어야 함(실시간 시각 금지)")
        if qid in self._commits:
            raise CommitRevealBlock(f"qid={qid!r} 이미 commit 됨 — 수정불가")
        answer_hash = compute_answer_hash(human_answer)
        self._commits[qid] = {
            "answer_hash": answer_hash,
            "norm_hash": _sha256_hex(_normalize(human_answer)),   # H2-2: 정규화 copy 탐지용
            "commit_ts": str(commit_ts),
            "actor": actor,
        }
        return {"answer_hash": answer_hash, "commit_ts": commit_ts, "committed": True}

    def is_committed(self, qid: str) -> bool:
        return qid in self._commits

    # ── reveal ───────────────────────────────────────────────────────────────
    def reveal_proposal(self, seal: str, nonce: str) -> Dict[str, object]:
        """
        제안 공개. 불변식(전부 fail-closed · 인자 의존 없음):
          1) seal 미존재 BLOCK.
          2) seal 에 바인딩된 qid 가 commit 되지 않았으면 BLOCK(prior-peek 차단).
             — H1: 차단이 호출 인자가 아니라 seal↔qid 바인딩으로 **강제**된다.
          3) nonce 불일치(위조) BLOCK — 재계산 대조.
        검증 통과 시 원문(text) 공개 + blind attestation 반환.
        attestation = {qid, blind_passed, copy_suspected, seal} — l1/l2 stamp 게이트 입력.
          - blind_passed: 사람이 commit 을 먼저 했고 봉인 검증 통과(항상 True 도달 조건).
          - copy_suspected: 사람 답변 hash 가 AI 제안 텍스트 hash 와 동일 = 베껴쓰기 의심(H2).
        """
        # 불변식 1: 봉인 존재
        rec = self._sealed.get(seal)
        if rec is None:
            raise CommitRevealBlock("seal 미존재 — reveal 불가")
        qid = rec["qid"]
        # 불변식 2: prior-peek 차단 (fail-closed — bound qid commit 강제)
        if not self.is_committed(qid):
            raise CommitRevealBlock(
                f"commit 전 reveal BLOCK — bound qid={qid!r} 미커밋(prior-peek, fail-closed)"
            )
        # 불변식 3: nonce 위조 차단 (저장 nonce 와 재도출 seal 둘 다 대조)
        if nonce != rec["nonce"]:
            raise CommitRevealBlock("nonce 불일치 — 위조 BLOCK")
        recomputed = compute_seal(rec["text"], nonce)
        if recomputed != seal:
            # 이론상 도달 불가(저장 nonce 일치 시). 방어적 BLOCK.
            raise CommitRevealBlock("seal 재도출 불일치 — 위조 BLOCK")
        # H2-2: 베껴쓰기 탐지 — 정규화 후 사람 답변 == AI 제안이면 blind 무효 신호
        #   (exact-hash 가 아니라 정규화 비교라 공백/대소문자 편집 회피 차단.
        #    구두점·단어치환·의미유사는 못 잡음 — 정규화 한계, 사람 도장이 최종 게이트)
        commit_rec = self._commits[qid]
        copy_suspected = (commit_rec["norm_hash"] == _sha256_hex(_normalize(rec["text"])))
        return {
            "revealed": True,
            "text": rec["text"],
            "seal": seal,
            "seal_ts": int(rec["seal_ts"]),
            "seal_verified": True,
            # ── blind attestation (l1/l2 stamp 게이트 입력) ──
            "qid": qid,
            "blind_passed": True,
            "copy_suspected": copy_suspected,
        }

    def _att_mac(self, qid: str, seal: str, blind_passed: bool, copy_suspected: bool) -> str:
        """attestation 위조 방지 MAC = HMAC(secret, qid|seal|blind|copy)."""
        canon = "%s\x1f%s\x1f%s\x1f%s" % (qid, seal, bool(blind_passed), bool(copy_suspected))
        return hmac.new(self._secret, canon.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue_attestation(self, reveal_result: Dict[str, object]) -> Dict[str, object]:
        """reveal 결과 → 위조 불가 attestation(H2-1). mac 으로 vault 발급 증명.
        stamp_l1/validate_l2_edge 는 같은 vault 의 verify_attestation 으로만 신뢰해야 함."""
        qid = reveal_result.get("qid")
        seal = reveal_result.get("seal")
        bp = bool(reveal_result.get("blind_passed"))
        cs = bool(reveal_result.get("copy_suspected"))
        return {"qid": qid, "seal": seal, "blind_passed": bp, "copy_suspected": cs,
                "mac": self._att_mac(qid, seal, bp, cs)}

    def verify_attestation(self, att: object) -> bool:
        """attestation 검증(H2-1). mac 재계산 대조(위조 차단) + blind 통과 + copy 의심 아님.
        손으로 만든 dict 는 mac 불일치로 reject. stamp/validate 게이트가 이 함수만 신뢰."""
        if not isinstance(att, dict):
            return False
        qid = att.get("qid"); seal = att.get("seal")
        bp = att.get("blind_passed"); cs = att.get("copy_suspected")
        expected = self._att_mac(qid, seal, bool(bp), bool(cs))
        if not hmac.compare_digest(expected, str(att.get("mac", ""))):
            return False
        return bp is True and cs is not True


# ─────────────────────────────────────────────────────────────────────────────
# selftest (결정론적 — 시드/주입 ts, 난수·실시간 시각 없음)
# ─────────────────────────────────────────────────────────────────────────────
def _selftest() -> bool:
    results = []

    def check(name: str, cond: bool) -> None:
        results.append((name, bool(cond)))

    NONCE = "deterministic-nonce-0001"
    TEXT = "AI 제안: 봉우리 추천가를 X로 설정하자"
    QID = "q-001"
    SECRET = "selftest-vault-secret"

    # 1) 정상 흐름: seal(qid 바인딩) -> commit -> reveal
    v = CommitRevealVault(SECRET)
    sealed = v.seal_proposal(TEXT, NONCE, seal_ts=1000, qid=QID)
    seal = sealed["seal"]
    check("seal_is_sha256(text+nonce)", seal == compute_seal(TEXT, NONCE))
    check("seal_hexlen64", len(seal) == 64)
    check("seal_ts_passthrough", sealed["seal_ts"] == 1000 and sealed["sealed"] is True)
    check("seal_binds_qid", sealed["qid"] == QID)
    check("seal_no_plaintext_leak", "text" not in sealed and "nonce" not in sealed)

    committed = v.commit_answer(QID, "사람 답: Y로 하겠다", commit_ts=2000, actor="human")
    check(
        "commit_answer_hash",
        committed["answer_hash"] == compute_answer_hash("사람 답: Y로 하겠다"),
    )
    check("commit_ts_passthrough", committed["commit_ts"] == 2000 and committed["committed"] is True)

    revealed = v.reveal_proposal(seal, NONCE)
    check("reveal_after_commit_ok", revealed["revealed"] is True)
    check("reveal_returns_text", revealed["text"] == TEXT)
    check("reveal_seal_verified", revealed["seal_verified"] is True and revealed["seal"] == seal)
    check("reveal_attestation_qid", revealed["qid"] == QID and revealed["blind_passed"] is True)
    check("reveal_copy_not_suspected", revealed["copy_suspected"] is False)
    att = v.issue_attestation(revealed)
    check("attestation_shape", att["blind_passed"] is True and att["copy_suspected"] is False
          and att["qid"] == QID and "mac" in att)
    # H2-1: 정상 attestation 은 같은 vault verify 통과
    check("attestation_verify_ok", v.verify_attestation(att) is True)
    # H2-1: 손으로 만든 dict(mac 없음/위조) reject
    forged_att = {"qid": QID, "seal": seal, "blind_passed": True, "copy_suspected": False,
                  "mac": "0" * 64}
    check("attestation_forged_reject", v.verify_attestation(forged_att) is False)
    check("attestation_no_mac_reject",
          v.verify_attestation({"qid": QID, "seal": seal, "blind_passed": True,
                                "copy_suspected": False}) is False)
    # H2-1: 다른 secret vault 가 발급한 att 는 교차 verify 실패(키 바인딩)
    other = CommitRevealVault("different-secret")
    check("attestation_cross_vault_reject", other.verify_attestation(att) is False)

    # 2) commit 전 reveal BLOCK (prior-peek) — H1 fail-closed: 인자 없이도 강제 차단
    v2 = CommitRevealVault(SECRET)
    s2 = v2.seal_proposal(TEXT, NONCE, seal_ts=1000, qid=QID)["seal"]
    blocked = False
    try:
        v2.reveal_proposal(s2, NONCE)  # 미커밋 — bound qid 검사로 BLOCK
    except CommitRevealBlock:
        blocked = True
    check("reveal_before_commit_BLOCK_failclosed", blocked)
    # commit 후엔 허용(차단이 prior-peek 한정임을 확인)
    v2.commit_answer(QID, "사람 답 늦게", commit_ts=2500, actor="human")
    check("reveal_unblocks_after_commit", v2.reveal_proposal(s2, NONCE)["revealed"] is True)

    # 2b) H2: 사람 답변이 AI 제안 텍스트와 동일 = 베껴쓰기 의심 → copy_suspected=True
    vC = CommitRevealVault(SECRET)
    sC = vC.seal_proposal(TEXT, NONCE, seal_ts=1000, qid=QID)["seal"]
    vC.commit_answer(QID, TEXT, commit_ts=2000, actor="human")  # AI 제안 그대로 베낌
    revC = vC.reveal_proposal(sC, NONCE)
    check("copy_suspected_when_answer_equals_proposal", revC["copy_suspected"] is True)

    # 3) nonce 위조 BLOCK
    v3 = CommitRevealVault(SECRET)
    s3 = v3.seal_proposal(TEXT, NONCE, seal_ts=1000, qid=QID)["seal"]
    v3.commit_answer(QID, "답", commit_ts=2000, actor="human")
    forged = False
    try:
        v3.reveal_proposal(s3, "wrong-nonce")
    except CommitRevealBlock:
        forged = True
    check("reveal_nonce_forgery_BLOCK", forged)
    check("reveal_correct_nonce_ok", v3.reveal_proposal(s3, NONCE)["revealed"] is True)

    # 4) 재도출 동일 (seal·answer_hash 결정론)
    seal_a = compute_seal(TEXT, NONCE)
    seal_b = compute_seal(TEXT, NONCE)
    check("seal_recompute_identical", seal_a == seal_b)
    ah_a = compute_answer_hash("동일 답변")
    ah_b = compute_answer_hash("동일 답변")
    check("answer_hash_recompute_identical", ah_a == ah_b)
    v4 = CommitRevealVault(SECRET)
    check("seal_vault_independent", v4.seal_proposal(TEXT, NONCE, seal_ts=9, qid=QID)["seal"] == seal_a)

    # 5) actor != human BLOCK
    v5 = CommitRevealVault(SECRET)
    actor_blocked = False
    try:
        v5.commit_answer(QID, "ai가 쓰려함", commit_ts=2000, actor="auto")
    except CommitRevealBlock:
        actor_blocked = True
    check("commit_actor_not_human_BLOCK", actor_blocked)

    # 6) 재커밋(수정) BLOCK
    v6 = CommitRevealVault(SECRET)
    v6.commit_answer(QID, "최초 답", commit_ts=2000, actor="human")
    recommit_blocked = False
    try:
        v6.commit_answer(QID, "바꾼 답", commit_ts=3000, actor="human")
    except CommitRevealBlock:
        recommit_blocked = True
    check("recommit_immutable_BLOCK", recommit_blocked)

    # 7) seal 미존재 reveal BLOCK
    v7 = CommitRevealVault(SECRET)
    v7.commit_answer(QID, "답", commit_ts=2000, actor="human")
    missing_blocked = False
    try:
        v7.reveal_proposal("0" * 64, NONCE)
    except CommitRevealBlock:
        missing_blocked = True
    check("reveal_missing_seal_BLOCK", missing_blocked)

    # 8) 빈 nonce 봉인 BLOCK
    empty_nonce_blocked = False
    try:
        CommitRevealVault(SECRET).seal_proposal(TEXT, "", seal_ts=1, qid=QID)
    except CommitRevealBlock:
        empty_nonce_blocked = True
    check("seal_empty_nonce_BLOCK", empty_nonce_blocked)

    # 9) qid 누락 봉인 BLOCK (H1: seal 은 qid 바인딩 필수)
    empty_qid_blocked = False
    try:
        CommitRevealVault(SECRET).seal_proposal(TEXT, NONCE, seal_ts=1, qid="")
    except CommitRevealBlock:
        empty_qid_blocked = True
    check("seal_empty_qid_BLOCK", empty_qid_blocked)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    gate = "GO" if passed == total else "STOP"
    print(f"RESULT: {passed}/{total}  GATE={gate}")
    print(f"GATE: {gate}")
    return passed == total


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        return 0 if _selftest() else 1
    print("hag_commit_reveal — commit-reveal 봉인 모듈")
    print("usage: python hag_commit_reveal.py --selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
