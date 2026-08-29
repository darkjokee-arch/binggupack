# -*- coding: utf-8 -*-
"""hag_orchestrator.py — Hybrid-AGI L0->L1->L2 통합 파이프라인 (신규).

기존 7모듈을 **import 재사용**해 사람 SAVE 원문(L0)부터 추론엣지(L2) 영구 승격까지를
하나의 commit-reveal blind 파이프라인으로 엮는다. 7모듈은 절대 수정하지 않는다.

  hag_blind_ledger   : seal_ts<commit_ts<reveal_ts hash-chain append-only (temp).
  hag_commit_reveal  : 제안 봉인 → 사람 답 먼저 commit → reveal → attestation 발급/검증.
  hag_l1_proposition : L0Raw → L1 명제(extract_l1_human / propose_l1_ai / stamp_l1).
  hag_l2_edge        : L1 사이 추론엣지(make_l2_edge / validate_l2_edge).
  hag_token_guard    : prefilter_pairs / incremental_candidates / budget_guard.
  hag_drift_metrics  : DriftGuard.evaluate 임계 → 자동 가역 보호행동.
  hag_meta_monitor   : diversity / track_trend 발화 정형화 추이(경고만).

영구금지 준수(전부 selftest 실측):
  - 운영 ledger(~/.binggupack/ledger.sqlite·capture_buffer.sqlite) 미접촉(읽기도 X).
    모든 상태는 tempfile/in-memory. selftest 가 mtime+size 전후 불변 실측.
  - 운영 store write 0. 신규 파일만. 7모듈 import 재사용·수정 0.
  - actor != 'human' 은 BLOCK(allowlist default-deny). AI 자동 영구화 0.
  - L0(사람 원문) 불변.
  - 결정론적: 실시간 시각/난수 금지. ts·nonce·secret 는 호출자 주입.

[핵심 H2-1 한계 보완] vault 강제 주입:
  orchestrator 는 stamp/promote 의 attestation verifier 를 **항상 자기
  CommitRevealVault.verify_attestation 으로만** 주입한다. orchestrator 의 공개
  API 어디에도 verifier 인자를 노출하지 않으므로, 호출자가 가짜 verifier
  (lambda: True)를 끼워 AI발 영구화를 통과시킬 구조적 여지가 없다.

CLI: python hag_orchestrator.py --selftest  ->  'GATE: GO' | 'GATE: STOP'
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── 7모듈 import 재사용 (수정 0) ──────────────────────────────────────────────
import hag_blind_ledger as bl
import hag_commit_reveal as cr
import hag_l1_proposition as l1
import hag_l2_edge as l2
import hag_token_guard as tg
import hag_drift_metrics as dm
import hag_meta_monitor as mm
import hag_keyring as kr   # 사용자별 vault secret 자동생성/로드(고정값 제거)


class OrchestratorBlock(Exception):
    """파이프라인 불변식 위반(actor·prior-peek·copy·위조·미검증 영구화)."""


class HybridAGIOrchestrator:
    """L0->L1->L2 E2E 파이프라인 통합기.

    내부에 자기 소유 CommitRevealVault 하나를 들고, blind 대조의 verifier 를
    외부에 절대 노출하지 않고 자기 vault 로만 강제 주입한다(vault_forced).

    상태(L0/L1/L2/blind ledger)는 전부 tempfile/in-memory. 운영 store 미접촉.
    """

    def __init__(self, vault_secret=None, blind_ledger_path=None, home_dir=None):
        # vault_secret = attestation HMAC 키. 평문 노출 금지.
        #   - None 이면 keyring 으로 사용자별 secret 을 자동 로드/생성(고정값 제거).
        #     공개 도구라 코드 고정 키를 박으면 위조 가능 → 각 사용자 머신 키 사용.
        #   - 결정론 selftest 는 주입형(고정 secret)으로 호출.
        # home_dir = keyring secret 보관 디렉터리(주입 가능). selftest 는 temp HOME.
        #   기본 None → keyring.default_home_dir()(~/.binggupack/hybrid_agi/, repo 밖).
        # blind_ledger_path = 호출자가 넘긴 temp SQLite 경로(운영 ledger 경로 금지).
        if vault_secret is None:
            # 고정값 제거: 사용자별 키를 keyring 에서 로드(없으면 생성).
            vault_secret = kr.get_or_create_secret(home_dir)
        if not vault_secret or not isinstance(vault_secret, str):
            raise OrchestratorBlock("vault_secret 필수 — attestation 위조 방지 키")
        if not blind_ledger_path or not isinstance(blind_ledger_path, str):
            raise OrchestratorBlock("blind_ledger_path 필수 — temp SQLite 경로(운영 ledger 금지)")
        # 운영 ledger 경로 차단(방어) — ~/.binggupack 하위는 거부.
        home_bp = os.path.normcase(os.path.abspath(os.path.expanduser("~/.binggupack")))
        if os.path.normcase(os.path.abspath(blind_ledger_path)).startswith(home_bp):
            raise OrchestratorBlock("운영 ledger 디렉터리(~/.binggupack) 경로 금지 — temp 만 허용")

        self._vault = cr.CommitRevealVault(vault_secret)   # 자기 소유 vault (verifier 출처)
        self._ledger = bl.open_ledger(blind_ledger_path)   # temp blind ledger
        self._l0 = {}     # l0_id -> L0Raw
        self._l1 = {}     # l1_id -> L1Proposition (도장 후 영구 객체 포함)
        self._l2 = {}     # edge_id -> dict (validate 결과 부착)
        self._drift = dm.DriftGuard()  # 임계 → 자동 가역 보호행동
        self._utterances = []          # 사람 발화 추이(meta_monitor 입력)

    # ── L0: 사람 SAVE 원문 (불변) ───────────────────────────────────────────
    def save_l0(self, l0_id, raw, created_at, is_human_utterance=True):
        """사람 SAVE 원문 노드 등록. L0 불변. actor 는 항상 사람(AI 가 L0 생성 0)."""
        if l0_id in self._l0:
            raise OrchestratorBlock("L0 중복 id: %s" % l0_id)
        node = l1.L0Raw(l0_id=l0_id, raw=raw, created_at=created_at)
        self._l0[l0_id] = node
        if is_human_utterance and isinstance(raw, str):
            self._utterances.append(raw)   # meta_monitor 정형화 추이용
        return node

    # ── L1: 사람 명시 추출 (자체 영구) ───────────────────────────────────────
    def extract_l1(self, l0_id, l1_id, source_span, created_at, proposition=None):
        """사람 명시 추출 → L1 자체 영구. blind 불필요(사람이 직접 골랐음)."""
        l0 = self._require_l0(l0_id)
        prop = l1.extract_l1_human(l0, l1_id, source_span, created_at, proposition=proposition)
        self._l1[l1_id] = prop
        return prop

    # ── L1: AI 제안 (휘발) ──────────────────────────────────────────────────
    def propose_l1(self, l0_id, l1_id, proposition, source_span, created_at):
        """AI 제안 L1 — 도장 전 비영구(휘발). self._l1 에 candidate 로만 보관."""
        l0 = self._require_l0(l0_id)
        prop = l1.propose_l1_ai(l0, l1_id, proposition, source_span, created_at)
        self._l1[l1_id] = prop  # 휘발 candidate(is_permanent()=False)
        return prop

    # ── blind 대조 1회분 (질문 → 봉인 → 사람 먼저 답 → reveal → attestation) ──
    def blind_stamp_l1(self, l1_id, qid, nonce, seal_ts,
                       human_answer, commit_ts, reveal_ts, actor="human",
                       result_label="l1_stamp", edited=False):
        """AI 제안 L1 을 blind 대조로 영구화.

        흐름(시각 강제 seal_ts<commit_ts<reveal_ts):
          1) 봉인 — AI 제안 문장을 vault.seal_proposal(text,nonce,seal_ts,qid).
          2) 사람 먼저 답 — vault.commit_answer(qid,human_answer,commit_ts,actor).
             actor != human 이면 vault 가 BLOCK(prior 답 베끼기 방지).
          3) reveal — commit 이후에만 vault.reveal_proposal(seal,nonce).
          4) attestation 발급 — vault.issue_attestation(reveal).
          5) 도장 — stamp_l1(..., verifier=self._vault.verify_attestation) [vault 강제].
          6) blind ledger append (seal_ts<commit_ts<reveal_ts, actor=human).
        copy_suspected/위조/미커밋 은 전부 차단(자기기만 0).
        """
        prop = self._require_l1(l1_id)
        if prop.extracted_by != l1.EXTRACT_AI:
            raise OrchestratorBlock("blind 대조는 AI 제안(ai_inferred) 전용 — human 추출은 자체 영구")
        if actor != "human":
            # vault 도 막지만, 진입 단계에서 명시 BLOCK(allowlist default-deny).
            raise OrchestratorBlock("actor=%r BLOCK — 사람만 commit 가능" % actor)

        # 1) 봉인 (제안 문장 = prop.proposition)
        sealed = self._vault.seal_proposal(prop.proposition, nonce, seal_ts=seal_ts, qid=qid)
        seal = sealed["seal"]
        # 2) 사람 먼저 답 (commit). prior-peek 차단은 vault 가 강제.
        committed = self._vault.commit_answer(qid, human_answer, commit_ts=commit_ts, actor=actor)
        # 3) reveal (commit 후에만 — vault fail-closed)
        revealed = self._vault.reveal_proposal(seal, nonce)
        # 4) attestation 발급
        att = self._vault.issue_attestation(revealed)
        # 5) 도장 — verifier 는 **항상** 자기 vault.verify_attestation (외부 주입 차단)
        stamped = l1.stamp_l1(
            prop, "human", edited=edited,
            attestation=att, verifier=self._vault.verify_attestation,
        )
        self._l1[l1_id] = stamped  # 영구화된 L1 로 교체
        # 6) blind ledger append (temp · seal_ts<commit_ts<reveal_ts · actor=human)
        led_row = self._ledger.append({
            "seal": seal,
            "seal_ts": seal_ts,
            "answer_hash": committed["answer_hash"],
            "commit_ts": commit_ts,
            "reveal_ts": reveal_ts,
            "actor": actor,
            "result": result_label,
        })
        return {
            "l1_id": l1_id,
            "permanent": stamped.is_permanent(),
            "copy_suspected": revealed["copy_suspected"],
            "attestation_verified": self._vault.verify_attestation(att),
            "ledger_seq": led_row["seq"],
            "stamped": stamped,
        }

    # ── L2: 추론엣지 후보 (휘발) ─────────────────────────────────────────────
    def make_edge(self, edge_id, relation, src_l1, tgt_l1, confidence,
                  evidence_refs, origin, counterevidence=None):
        """L2 추론엣지 후보 조립(candidate 휘발). 영구화는 promote_edge 로만."""
        edge = l2.make_l2_edge(edge_id, relation, src_l1, tgt_l1, confidence,
                               evidence_refs, origin, counterevidence=counterevidence)
        self._l2[edge_id] = edge
        return edge

    # ── L2: blind 대조로 추론엣지 영구 승격 ─────────────────────────────────
    def blind_promote_edge(self, edge_id, nodes_by_id, qid, nonce, seal_ts,
                           human_answer, commit_ts, reveal_ts, actor="human",
                           result_label="l2_promote"):
        """AI발 L2 엣지를 blind 대조로 영구 승격.

        blind_stamp_l1 과 동형: 봉인(엣지 명제) → 사람 먼저 답 → reveal → attestation →
        validate_l2_edge(..., attestation_verifier=self._vault.verify_attestation) [vault 강제].
        승격 의도(promotion_allowed=True·status=confirmed·stamped_by=human)를 세팅하되,
        attestation_verifier 는 절대 외부에서 받지 않는다(vault_forced).
        """
        edge = self._require_l2(edge_id)
        if actor != "human":
            raise OrchestratorBlock("actor=%r BLOCK — 사람만 commit 가능" % actor)

        # 봉인 대상 = 엣지 명제(관계 진술). 결정론 직렬화.
        edge_text = "%s(%s->%s)" % (edge.get("relation"), edge.get("src_l1"), edge.get("tgt_l1"))
        sealed = self._vault.seal_proposal(edge_text, nonce, seal_ts=seal_ts, qid=qid)
        seal = sealed["seal"]
        committed = self._vault.commit_answer(qid, human_answer, commit_ts=commit_ts, actor=actor)
        revealed = self._vault.reveal_proposal(seal, nonce)
        att = self._vault.issue_attestation(revealed)

        # 승격 의도 세팅 + 사람 도장 + attestation 부착.
        promo_edge = dict(edge)
        promo_edge["stamped_by"] = "human"
        promo_edge["promotion_allowed"] = True
        promo_edge["status"] = "confirmed"
        promo_edge["attestation"] = att

        # validate — verifier 는 **항상** 자기 vault.verify_attestation (외부 주입 0)
        verdict = l2.validate_l2_edge(
            promo_edge, nodes_by_id,
            attestation_verifier=self._vault.verify_attestation,
        )
        if verdict["verdict"] != "PASS" or not verdict["permanent"]:
            # 영구화 실패 → 휘발 candidate 유지(원본 불변). ledger append 안 함.
            return {"edge_id": edge_id, "permanent": False, "verdict": verdict,
                    "copy_suspected": revealed["copy_suspected"]}

        self._l2[edge_id] = promo_edge  # 영구 승격된 엣지로 교체
        led_row = self._ledger.append({
            "seal": seal, "seal_ts": seal_ts,
            "answer_hash": committed["answer_hash"],
            "commit_ts": commit_ts, "reveal_ts": reveal_ts,
            "actor": actor, "result": result_label,
        })
        return {"edge_id": edge_id, "permanent": True, "verdict": verdict,
                "copy_suspected": revealed["copy_suspected"], "ledger_seq": led_row["seq"]}

    # ── token_guard 연결: 후보 좁힘 ─────────────────────────────────────────
    def candidate_pairs(self, nodes):
        """전수 N^2 금지 — 구조 신호로 후보쌍만(prefilter)."""
        return tg.prefilter_pairs(nodes)

    def incremental_pairs(self, new_node, graph):
        """새 노드 주변만(증분). 기존끼리 재비교 0."""
        return tg.incremental_candidates(new_node, graph)

    def budget_guard(self, daily_tokens, cap):
        """일일 토큰 cap 초과 hard stop."""
        return tg.budget_guard(daily_tokens, cap)

    # ── drift_metrics 연결: 임계 → 자동 행동 ────────────────────────────────
    def evaluate_drift(self, snapshot):
        """오염 임계 평가 → freeze/suspend 자동 가역 행동(L0 불변·삭제0)."""
        return self._drift.evaluate(snapshot)

    @property
    def ai_proposals_frozen(self):
        return self._drift.ai_proposals_frozen

    # ── meta_monitor 연결: 발화 정형화 추이(경고만) ─────────────────────────
    def diversity_now(self):
        """현재까지 누적 사람 발화의 다양성 점수(경고만 · 행동 0)."""
        return mm.diversity(self._utterances)

    def trend(self, series, window=3, baseline=None, margin=0.05):
        """다양성 추이 — 하락 시 warn(경고만)."""
        return mm.track_trend(series, window=window, baseline=baseline, margin=margin)

    # ── 조회/정리 ───────────────────────────────────────────────────────────
    def ledger_verify(self):
        return self._ledger.verify_chain()

    def ledger_rows(self):
        return self._ledger.rows()

    def get_l1(self, l1_id):
        return self._l1.get(l1_id)

    def get_edge(self, edge_id):
        return self._l2.get(edge_id)

    def close(self):
        self._ledger.close()

    # ── 내부 ────────────────────────────────────────────────────────────────
    def _require_l0(self, l0_id):
        if l0_id not in self._l0:
            raise OrchestratorBlock("L0 미존재: %s" % l0_id)
        return self._l0[l0_id]

    def _require_l1(self, l1_id):
        if l1_id not in self._l1:
            raise OrchestratorBlock("L1 미존재: %s" % l1_id)
        return self._l1[l1_id]

    def _require_l2(self, edge_id):
        if edge_id not in self._l2:
            raise OrchestratorBlock("L2 엣지 미존재: %s" % edge_id)
        return self._l2[edge_id]


# ─────────────────────────────────────────────────────────────────────────────
# selftest (결정론 · 운영 미접촉 · 시드/주입 ts)
# ─────────────────────────────────────────────────────────────────────────────
def _operating_snapshot():
    """운영 ledger 파일 mtime+size 스냅샷(미접촉 실측용). 파일 read 0 — stat 만."""
    snap = {}
    base = os.path.expanduser("~/.binggupack")
    for name in ("ledger.sqlite", "capture_buffer.sqlite"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            st = os.stat(p)
            snap[name] = (st.st_mtime, st.st_size)
        else:
            snap[name] = None
    return snap


def _node(nid, kind):
    return {"id": nid, "properties": {"label_kind": kind, "candidate": True}}


def _selftest():
    results = []

    def ck(name, cond):
        results.append((name, bool(cond)))

    # ── 운영 ledger 미접촉 실측: 전 스냅샷 ──
    op_before = _operating_snapshot()

    tmpdir = tempfile.mkdtemp(prefix="hag_orch_")
    SECRET = "selftest-orch-secret"
    NONCE = "deterministic-nonce-orch-0001"

    # ===== 1) 정상 E2E: SAVE→AI제안→사람 먼저 답(다른 내용)→reveal→도장→L2 영구 =====
    led_path = os.path.join(tmpdir, "blind_main.sqlite")
    orch = HybridAGIOrchestrator(SECRET, led_path)

    raw = "빌드가 깨져 있다. 배포 전 확인하자."
    orch.save_l0("L0-1", raw, created_at=1000)
    # 사람 명시 추출 L1(증거성 노드 역할) — 자체 영구
    p_ev = orch.extract_l1("L0-1", "L1-ev", (0, 9), created_at=1000)  # "빌드가 깨져 있다"
    ck("human_extract_permanent", p_ev.is_permanent() is True)

    # AI 제안 L1(판단 노드 역할) — 휘발
    p_ai = orch.propose_l1("L0-1", "L1-j1", "배포 전 빌드를 확인해야 한다", (11, len(raw)),
                           created_at=1000)
    ck("ai_propose_not_permanent", p_ai.is_permanent() is False)

    # blind 대조: 사람이 AI 제안과 **다른 내용**으로 먼저 답 → 도장 영구화
    r = orch.blind_stamp_l1(
        "L1-j1", qid="q-l1", nonce=NONCE, seal_ts=1000,
        human_answer="확인 후 배포 진행한다(독립 판단)", commit_ts=2000, reveal_ts=3000)
    ck("blind_stamp_permanent", r["permanent"] is True)
    ck("blind_stamp_copy_not_suspected", r["copy_suspected"] is False)
    ck("blind_stamp_attestation_verified", r["attestation_verified"] is True)
    ck("blind_stamp_l1_now_permanent", orch.get_l1("L1-j1").is_permanent() is True)
    ck("ledger_chain_ok_after_l1", orch.ledger_verify() is True)

    # L2 엣지: depends_on (판단 L1-j1 → 상태/개념). 매트릭스: depends_on src=판단.
    nodes = {
        "L1-j1": _node("L1-j1", "판단"),
        "L1-st": _node("L1-st", "상태"),
        "L1-ev": _node("L1-ev", "증거"),
    }
    orch.save_l0("L0-2", "상태 노드 원문", created_at=1000)
    orch.extract_l1("L0-2", "L1-st", (0, 2), created_at=1000)
    orch.make_edge("e1", "depends_on", "L1-j1", "L1-st", 0.7, ["EVC-1"], origin="ai")
    pe = orch.blind_promote_edge(
        "e1", nodes, qid="q-l2", nonce=NONCE, seal_ts=4000,
        human_answer="이 의존관계는 타당(독립 판단)", commit_ts=5000, reveal_ts=6000)
    ck("l2_promote_permanent", pe["permanent"] is True)
    ck("l2_edge_status_confirmed", orch.get_edge("e1")["status"] == "confirmed")
    ck("ledger_chain_ok_after_l2", orch.ledger_verify() is True)
    # blind ledger 흐름 seal_ts<commit_ts<reveal_ts 2건 기록
    rows = orch.ledger_rows()
    ck("ledger_two_rows", len(rows) == 2)
    ck("ledger_temporal_flow",
       all(rw["seal_ts"] < rw["commit_ts"] < rw["reveal_ts"] for rw in rows))
    ck("ledger_actor_all_human", all(rw["actor"] == "human" for rw in rows))
    orch.close()

    # ===== 2) prior-peek 차단: commit 전 reveal 시도 BLOCK =====
    orch2 = HybridAGIOrchestrator(SECRET, os.path.join(tmpdir, "blind2.sqlite"))
    orch2.save_l0("L0-1", raw, created_at=1000)
    orch2.propose_l1("L0-1", "L1-p", "AI 제안", (0, 5), created_at=1000)
    # 직접 vault 봉인만 하고 commit 없이 reveal → vault fail-closed BLOCK
    sealed = orch2._vault.seal_proposal("AI 제안", NONCE, seal_ts=1000, qid="q-peek")
    peek_blocked = False
    try:
        orch2._vault.reveal_proposal(sealed["seal"], NONCE)  # 미커밋
    except cr.CommitRevealBlock:
        peek_blocked = True
    ck("prior_peek_before_commit_BLOCK", peek_blocked)
    orch2.close()

    # ===== 3) copy 차단: 사람이 AI 제안 베껴 commit → copy_suspected → 도장 차단 =====
    orch3 = HybridAGIOrchestrator(SECRET, os.path.join(tmpdir, "blind3.sqlite"))
    orch3.save_l0("L0-1", raw, created_at=1000)
    AI_TEXT = "배포 전 빌드를 확인해야 한다"
    orch3.propose_l1("L0-1", "L1-c", AI_TEXT, (11, len(raw)), created_at=1000)
    copy_blocked = False
    try:
        # 사람이 AI 제안 문장을 그대로 베껴 답 → reveal 에서 copy_suspected=True
        # → verify_attestation False → stamp_l1 PermissionError
        orch3.blind_stamp_l1("L1-c", qid="q-copy", nonce=NONCE, seal_ts=1000,
                             human_answer=AI_TEXT, commit_ts=2000, reveal_ts=3000)
    except PermissionError:
        copy_blocked = True
    ck("copy_suspected_stamp_BLOCK", copy_blocked)
    ck("copy_l1_still_not_permanent", orch3.get_l1("L1-c").is_permanent() is False)
    # copy 차단 시 ledger append 안 됨(영구화 실패)
    ck("copy_no_ledger_row", orch3.ledger_rows() == [])
    orch3.close()

    # ===== 4) attestation 위조 차단: 손으로 만든 dict → vault.verify reject → 도장 차단 =====
    orch4 = HybridAGIOrchestrator(SECRET, os.path.join(tmpdir, "blind4.sqlite"))
    forged_att = {"qid": "q", "seal": "s", "blind_passed": True,
                  "copy_suspected": False, "mac": "0" * 64}
    # vault 가 위조 dict reject (mac 불일치)
    ck("forged_attestation_reject", orch4._vault.verify_attestation(forged_att) is False)
    # 위조 dict 로 직접 stamp_l1 호출해도 vault verifier 가 차단
    orch4.save_l0("L0-1", raw, created_at=1000)
    p4 = orch4.propose_l1("L0-1", "L1-f", "AI 제안", (0, 5), created_at=1000)
    forge_blocked = False
    try:
        l1.stamp_l1(p4, "human", attestation=forged_att,
                    verifier=orch4._vault.verify_attestation)
    except PermissionError:
        forge_blocked = True
    ck("forged_attestation_stamp_BLOCK", forge_blocked)
    orch4.close()

    # ===== 5) AI 자동 0: actor!=human · verifier 없이 영구화 시도 전부 차단 =====
    orch5 = HybridAGIOrchestrator(SECRET, os.path.join(tmpdir, "blind5.sqlite"))
    orch5.save_l0("L0-1", raw, created_at=1000)
    orch5.propose_l1("L0-1", "L1-a", "AI 제안", (0, 5), created_at=1000)
    # 5a) actor != human 진입 BLOCK
    actor_blocked = False
    try:
        orch5.blind_stamp_l1("L1-a", qid="q-a", nonce=NONCE, seal_ts=1000,
                             human_answer="답", commit_ts=2000, reveal_ts=3000, actor="auto")
    except OrchestratorBlock:
        actor_blocked = True
    ck("actor_not_human_BLOCK", actor_blocked)
    ck("actor_block_l1_not_permanent", orch5.get_l1("L1-a").is_permanent() is False)

    # 5b) vault_forced — orchestrator 공개 API 어디에도 verifier 인자 없음.
    #     stamp/promote 시그니처에 외부 verifier/attestation_verifier 노출 0.
    import inspect
    stamp_sig = inspect.signature(HybridAGIOrchestrator.blind_stamp_l1)
    promo_sig = inspect.signature(HybridAGIOrchestrator.blind_promote_edge)
    forbidden_params = {"verifier", "attestation_verifier", "attestation"}
    ck("vault_forced_no_verifier_param_l1",
       not (set(stamp_sig.parameters) & forbidden_params))
    ck("vault_forced_no_verifier_param_l2",
       not (set(promo_sig.parameters) & forbidden_params))

    # 5c) vault_forced 실증 — 가짜 verifier(lambda:True)를 끼울 수 없음.
    #     l2 의 raw validate 는 위조 att 라도 fake verifier 면 PASS 하지만,
    #     orchestrator 는 자기 vault 만 쓰므로 fake verifier 영구화 경로가 없다.
    orch5.make_edge("e_fake", "depends_on", "L1-j1", "L1-st", 0.7, ["EVC-1"], origin="ai")
    fake_edge = dict(orch5.get_edge("e_fake"))
    fake_edge["stamped_by"] = "human"
    fake_edge["promotion_allowed"] = True
    fake_edge["status"] = "confirmed"
    fake_edge["attestation"] = forged_att  # 위조 att
    nodes5 = {"L1-j1": _node("L1-j1", "판단"), "L1-st": _node("L1-st", "상태")}
    # 만약 누가 fake verifier 를 직접 끼우면 raw 모듈은 통과(취약점) — 대조용.
    raw_fake_pass = l2.validate_l2_edge(fake_edge, nodes5, attestation_verifier=lambda a: True)
    ck("raw_module_fake_verifier_would_pass", raw_fake_pass["permanent"] is True)
    # orchestrator 경로(자기 vault)는 위조 att 를 reject → 영구화 0.
    real_v = orch5._vault.verify_attestation
    orch_path = l2.validate_l2_edge(fake_edge, nodes5, attestation_verifier=real_v)
    ck("orch_vault_rejects_forged", orch_path["permanent"] is False)
    orch5.close()

    # ===== 6) drift 발동: ai_node_ratio 등 임계 초과 → freeze 발동 =====
    orch6 = HybridAGIOrchestrator(SECRET, os.path.join(tmpdir, "blind6.sqlite"))
    dirty = {"total_nodes": 100, "ai_nodes": 50,  # 0.50 > 0.35
             "total_edges": 100, "ai_edges": 10,
             "promotions_total": 100, "promotions_blind_miss": 5, "by_source": {}}
    drift_r = orch6.evaluate_drift(dirty)
    ck("drift_freeze_triggered", "freeze_ai_proposals" in drift_r["actions_taken"])
    ck("drift_frozen_state", orch6.ai_proposals_frozen is True)
    ck("drift_reversible_no_delete",
       drift_r["reversible"] is True and drift_r["deletes_data"] is False
       and drift_r["l0_untouched"] is True)
    orch6.close()

    # ===== 7) token_guard 연결: 후보 좁힘 + budget hard stop =====
    orch7 = HybridAGIOrchestrator(SECRET, os.path.join(tmpdir, "blind7.sqlite"))
    tnodes = [
        {"id": "A", "properties": {"bucket": "2026-24", "topics": ["graph"]}},
        {"id": "B", "properties": {"bucket": "2026-24", "topics": ["graph"]}},
        {"id": "C", "properties": {"bucket": "2026-30", "topics": ["billing"]}},
    ]
    pairs = orch7.candidate_pairs(tnodes)
    pset = {(p["a"], p["b"]) for p in pairs}
    ck("token_prefilter_keeps_related", ("A", "B") in pset)
    ck("token_prefilter_drops_unrelated", ("A", "C") not in pset and ("B", "C") not in pset)
    inc = orch7.incremental_pairs(
        {"id": "NEW", "properties": {"bucket": "2026-24", "topics": ["graph"]}}, tnodes)
    ck("token_incremental_new_only", all(p["a"] == "NEW" for p in inc) and len(inc) >= 1)
    budget_stop = False
    try:
        orch7.budget_guard(1500, 1000)
    except tg.TokenBudgetExceeded:
        budget_stop = True
    ck("token_budget_hard_stop", budget_stop)
    ck("token_budget_within", orch7.budget_guard(300, 1000) == 700)
    orch7.close()

    # ===== 8) meta_monitor 연결: diversity 추이(경고만) =====
    orch8 = HybridAGIOrchestrator(SECRET, os.path.join(tmpdir, "blind8.sqlite"))
    orch8.save_l0("U1", "알파 베타 감마 델타", created_at=1)
    orch8.save_l0("U2", "엡실론 제타 에타 세타", created_at=2)
    div = orch8.diversity_now()
    ck("meta_diversity_scores", 0.0 <= div["score"] <= 1.0 and div["n_texts"] == 2)
    down = orch8.trend([0.9, 0.85, 0.7, 0.5, 0.3], window=3, baseline=0.85, margin=0.05)
    ck("meta_trend_warn_on_decline", down["warn"] is True and down["level"] == "warn")
    # 경고만 — 반환에 행동/부작용 키 0
    ck("meta_trend_warn_only",
       not (set(down.keys()) & {"action", "exec", "write", "block", "kill", "delete"}))
    orch8.close()

    # ===== 9) 운영 ledger 경로 거부(방어) =====
    op_path_blocked = False
    try:
        HybridAGIOrchestrator(SECRET, os.path.expanduser("~/.binggupack/x.sqlite"))
    except OrchestratorBlock:
        op_path_blocked = True
    ck("operating_ledger_path_rejected", op_path_blocked)

    # ===== 9b) keyring 자동 secret(고정값 제거) — vault_secret=None → temp HOME 로드 =====
    kr_home = os.path.join(tmpdir, "kr_home", ".binggupack", "hybrid_agi")
    orch_kr = HybridAGIOrchestrator(
        vault_secret=None, blind_ledger_path=os.path.join(tmpdir, "blind_kr.sqlite"),
        home_dir=kr_home)
    orch_kr.save_l0("L0-1", raw, created_at=1000)
    orch_kr.propose_l1("L0-1", "L1-k", "배포 전 빌드를 확인해야 한다", (11, len(raw)), created_at=1000)
    rk = orch_kr.blind_stamp_l1(
        "L1-k", qid="q-k", nonce=NONCE, seal_ts=1000,
        human_answer="독립적인 사람 답", commit_ts=2000, reveal_ts=3000)
    ck("keyring_default_secret_works", rk["permanent"] is True)
    # secret 파일이 temp HOME 에 생성됐는지(실제 ~/.binggupack 아님)
    ck("keyring_secret_file_in_temp_home",
       os.path.exists(kr.secret_path(kr_home)))
    orch_kr.close()

    # ===== 10) 결정론: 동일 입력 두 실행 → 동일 seal/도장 결과 =====
    def _run_once():
        o = HybridAGIOrchestrator(SECRET, os.path.join(
            tmpdir, "det_%d.sqlite" % _run_once.counter))
        _run_once.counter += 1
        o.save_l0("L0-1", raw, created_at=1000)
        o.propose_l1("L0-1", "L1-d", "배포 전 빌드를 확인해야 한다", (11, len(raw)), created_at=1000)
        res = o.blind_stamp_l1("L1-d", qid="q-d", nonce=NONCE, seal_ts=1000,
                               human_answer="독립적인 사람 답", commit_ts=2000, reveal_ts=3000)
        rows = o.ledger_rows()
        o.close()
        return res["permanent"], rows[0]["seal"], rows[0]["entry_hash"]
    _run_once.counter = 0
    a1 = _run_once()
    a2 = _run_once()
    ck("deterministic_same_seal_and_hash", a1 == a2)

    # ── 운영 ledger 미접촉 실측: 후 스냅샷 비교 ──
    op_after = _operating_snapshot()
    operating_untouched = (op_before == op_after)
    ck("operating_ledger_untouched_mtime_size", operating_untouched)

    # ── 결과 집계 ──
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("RESULT: %d/%d" % (passed, total))
    print("operating_untouched: %s" % operating_untouched)
    # vault_forced 증명: 5b 두 항목 + 5c orchestrator reject 통과
    vf_names = {"vault_forced_no_verifier_param_l1", "vault_forced_no_verifier_param_l2",
                "orch_vault_rejects_forged"}
    vault_forced = all(ok for nm, ok in results if nm in vf_names) and \
        len([nm for nm, _ in results if nm in vf_names]) == 3
    print("vault_forced: %s" % vault_forced)
    gate = "GO" if passed == total else "STOP"
    print("GATE: %s" % gate)
    return passed, total, gate, operating_untouched, vault_forced


def main():
    args = sys.argv[1:]
    if args and args[0] == "--selftest":
        p, t, g, ou, vf = _selftest()
        return 0 if g == "GO" else 1
    print("hag_orchestrator — Hybrid-AGI L0->L1->L2 통합 파이프라인")
    print("usage: python hag_orchestrator.py --selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
