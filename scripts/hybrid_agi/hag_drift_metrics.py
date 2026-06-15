# -*- coding: utf-8 -*-
"""hag_drift_metrics.py — 오염 행동규칙 지표 (Hybrid-AGI drift guardrails).

핵심 차이: 이건 "관측 대시보드"가 아니라 "임계 -> 자동 행동" 행동규칙이다.
지표가 보수적 임계를 넘으면 즉시 가역 보호행동(freeze_ai_proposals / suspend_source)을
발동한다. 사람이 보고 누르는 게 아니라, 코드가 자동으로 AI 오염 확산을 멈춘다.

지표 (전부 결정론적·주입 입력만 — 운영 ledger 미접촉):
  - ai_node_ratio          : AI가 만든 노드 / 전체 노드. (L0 사람 원문 비율이 줄면 오염)
  - ai_edge_ratio          : AI가 만든 엣지 / 전체 엣지.
  - blind_miss_promotion_rate : evidence(원문 근거) 없이 승격된 비율 (맹목 승격 = 환각 확산 위험).
  - later_deprecated_rate  : 승격됐다가 나중에 deprecated 된 비율 (by source — source별 신뢰도).

행동 (가역 · L0 불변 · 원문 삭제 절대 X):
  - freeze_ai_proposals()  : AI 신규 제안(TTL 휘발 후보) 동결. 기존 L0/사람 노드는 무손상.
  - suspend_source(src)    : 특정 source(예: 특정 AI 어댑터)의 제안만 일시 정지. 가역.
  두 행동 모두 "원문/노드/엣지 삭제" 0. 플래그 토글만 — unfreeze/resume 으로 즉시 복구.

영구금지 준수:
  - 운영 ledger / capture_buffer 읽기도 안 함. 입력은 호출측이 주입한 카운트 dict 뿐.
  - actor!='human' 노드/엣지를 영구 승격시키지 않음(이 모듈은 오히려 그걸 막는 브레이크).
  - 결정론적: 시각/난수 미사용. 임계는 보수적 상수로 코드에 박음.
"""
import sys


# ── 보수적 기본 임계 (코드에 박음 · 넘으면 자동 행동) ──────────────────────────
# 보수적 = "조금만 오염돼도 일찍 멈춘다". 사람 원문(L0) 우위를 강하게 보장.
DEFAULT_THRESHOLDS = {
    # AI 노드 비율이 35% 넘으면 사람 원문 우위가 흔들림 → AI 제안 동결.
    "ai_node_ratio": 0.35,
    # AI 엣지 비율은 노드보다 더 보수적(관계 오염이 더 위험) → 30%.
    "ai_edge_ratio": 0.30,
    # 근거 없는 맹목 승격이 20% 넘으면 환각 확산 → 동결.
    "blind_miss_promotion_rate": 0.20,
    # 승격 후 철회율이 25% 넘는 source 는 신뢰 불가 → 해당 source 정지.
    "later_deprecated_rate": 0.25,
}


def _safe_ratio(num, den):
    """0/0 = 0.0 (오염 없음). 음수/비정상 입력은 0 으로 클램프 (결정론·방어)."""
    num = max(0, int(num))
    den = max(0, int(den))
    if den == 0:
        return 0.0
    r = num / den
    if r < 0.0:
        return 0.0
    if r > 1.0:
        return 1.0
    return r


def compute_metrics(snapshot):
    """주입된 카운트 snapshot 으로 지표 계산. read-only · write 0 · 결정론.

    snapshot 예:
      {
        "total_nodes": 100, "ai_nodes": 20,
        "total_edges": 50,  "ai_edges": 10,
        "promotions_total": 30, "promotions_blind_miss": 4,
        "by_source": {                 # source 별 승격/철회
            "ai:gemini":  {"promoted": 10, "later_deprecated": 1},
            "ai:ollama":  {"promoted": 8,  "later_deprecated": 3},
        },
      }
    L0(사람 원문)은 total 에 포함되되 ai_* 에는 절대 들어가지 않음(호출측 책임).
    """
    s = snapshot or {}
    by_source = s.get("by_source") or {}
    later_dep_by_source = {}
    for src, c in by_source.items():
        promoted = (c or {}).get("promoted", 0)
        dep = (c or {}).get("later_deprecated", 0)
        later_dep_by_source[src] = _safe_ratio(dep, promoted)

    # 전역 later_deprecated_rate (모든 source 합산) — 보고용 보조값.
    tot_promoted = sum(max(0, int((c or {}).get("promoted", 0))) for c in by_source.values())
    tot_dep = sum(max(0, int((c or {}).get("later_deprecated", 0))) for c in by_source.values())

    return {
        "ai_node_ratio": _safe_ratio(s.get("ai_nodes", 0), s.get("total_nodes", 0)),
        "ai_edge_ratio": _safe_ratio(s.get("ai_edges", 0), s.get("total_edges", 0)),
        "blind_miss_promotion_rate": _safe_ratio(
            s.get("promotions_blind_miss", 0), s.get("promotions_total", 0)
        ),
        "later_deprecated_rate": _safe_ratio(tot_dep, tot_promoted),
        "later_deprecated_rate_by_source": later_dep_by_source,
    }


class DriftGuard:
    """오염 임계 감시 + 자동 가역 보호행동.

    핵심: 임계 초과 시 evaluate() 가 스스로 freeze/suspend 를 호출한다(자동 행동).
    모든 행동은 가역 — unfreeze_ai_proposals / resume_source / reset 으로 즉시 복구.
    어떤 행동도 노드/엣지/원문을 삭제하지 않음 (L0 불변 · 가역).
    """

    def __init__(self, thresholds=None):
        # 보수적 기본값 복사. 외부 덮어쓰기는 **더 엄격하게만**(완화 차단·강제).
        # 모든 임계는 비율 상한이라 값이 작을수록 빨리 발동 = 더 엄격. 더 큰 값(완화) 거부.
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            for k, v in thresholds.items():
                if k in self.thresholds and v > self.thresholds[k]:
                    raise ValueError(
                        "threshold 완화 금지: %s %s->%s (더 엄격(작게)만 허용)"
                        % (k, self.thresholds[k], v))
                self.thresholds[k] = v
        self._ai_proposals_frozen = False
        self._suspended_sources = set()
        self.actions_log = []   # 발동/복구 이력 (결정론·메모리만)

    # ── 자동 행동 (가역) ──────────────────────────────────────────────────
    def freeze_ai_proposals(self, reason="threshold_exceeded"):
        """AI 신규 제안(TTL 휘발 후보) 동결. L0/사람 노드·엣지·원문 무손상.
        멱등: 이미 동결이면 중복 로그 0."""
        if not self._ai_proposals_frozen:
            self._ai_proposals_frozen = True
            self.actions_log.append(("freeze_ai_proposals", reason))
        return {"ai_proposals_frozen": True, "reversible": True, "deletes_data": False}

    def unfreeze_ai_proposals(self):
        """동결 해제 (가역 복구)."""
        if self._ai_proposals_frozen:
            self._ai_proposals_frozen = False
            self.actions_log.append(("unfreeze_ai_proposals", "manual_or_recovered"))
        return {"ai_proposals_frozen": False, "reversible": True, "deletes_data": False}

    def suspend_source(self, source, reason="source_threshold_exceeded"):
        """특정 source 제안만 일시 정지 (가역). 노드 삭제 0."""
        if source not in self._suspended_sources:
            self._suspended_sources.add(source)
            self.actions_log.append(("suspend_source", "%s:%s" % (source, reason)))
        return {"suspended": source, "reversible": True, "deletes_data": False}

    def resume_source(self, source):
        """source 정지 해제 (가역 복구)."""
        if source in self._suspended_sources:
            self._suspended_sources.discard(source)
            self.actions_log.append(("resume_source", source))
        return {"resumed": source, "reversible": True, "deletes_data": False}

    def reset(self):
        """모든 보호행동 해제 — 완전 가역 복구 증명용."""
        self._ai_proposals_frozen = False
        self._suspended_sources = set()
        self.actions_log.append(("reset", "all_cleared"))

    # ── 상태 조회 ────────────────────────────────────────────────────────
    @property
    def ai_proposals_frozen(self):
        return self._ai_proposals_frozen

    @property
    def suspended_sources(self):
        return set(self._suspended_sources)

    def is_proposal_allowed(self, source=None):
        """제안 게이트. 동결 중이거나 source 정지 중이면 거부.
        주의: 이건 '신규 AI 제안 차단'일 뿐, 기존 L0/노드 읽기·보존엔 영향 0."""
        if self._ai_proposals_frozen:
            return False
        if source is not None and source in self._suspended_sources:
            return False
        return True

    # ── 평가 → 자동 행동 ──────────────────────────────────────────────────
    def evaluate(self, snapshot):
        """지표 계산 후 임계 초과 시 자동으로 보호행동 발동.
        반환: {metrics, breaches[], actions_taken[], frozen, suspended[]}.
        가역·L0불변·삭제0 보장."""
        metrics = compute_metrics(snapshot)
        breaches = []
        actions = []

        # 전역 지표 3종 초과 → AI 제안 동결.
        for key in ("ai_node_ratio", "ai_edge_ratio", "blind_miss_promotion_rate"):
            if metrics[key] > self.thresholds[key]:
                breaches.append({"metric": key, "value": metrics[key],
                                 "threshold": self.thresholds[key]})
        if breaches:
            self.freeze_ai_proposals(reason="|".join(b["metric"] for b in breaches))
            actions.append("freeze_ai_proposals")

        # source별 철회율 초과 → 해당 source만 정지 (정밀 차단).
        thr_dep = self.thresholds["later_deprecated_rate"]
        for src, rate in metrics["later_deprecated_rate_by_source"].items():
            if rate > thr_dep:
                breaches.append({"metric": "later_deprecated_rate", "source": src,
                                 "value": rate, "threshold": thr_dep})
                self.suspend_source(src, reason="dep_rate=%.3f" % rate)
                if "suspend_source" not in actions:
                    actions.append("suspend_source")

        return {
            "metrics": metrics,
            "breaches": breaches,
            "actions_taken": actions,
            "frozen": self._ai_proposals_frozen,
            "suspended": sorted(self._suspended_sources),
            "reversible": True,
            "l0_untouched": True,
            "deletes_data": False,
        }


# ── selftest (결정론 · 주입 입력만 · 운영 ledger 미접촉) ──────────────────────
def _selftest():
    ok = True

    def ck(c, m):
        nonlocal ok
        ok = ok and bool(c)
        print("  [%s] %s" % ("PASS" if c else "FAIL", m))

    # 1) 정상 범위 (임계 미만) → 동결/정지 0
    clean = {
        "total_nodes": 100, "ai_nodes": 10,        # 0.10 < 0.35
        "total_edges": 100, "ai_edges": 10,        # 0.10 < 0.30
        "promotions_total": 100, "promotions_blind_miss": 5,  # 0.05 < 0.20
        "by_source": {"ai:gemini": {"promoted": 50, "later_deprecated": 5}},  # 0.10 < 0.25
    }
    g = DriftGuard()
    r = g.evaluate(clean)
    ck(r["breaches"] == [], "정상 범위 → 위반 0")
    ck(r["actions_taken"] == [], "정상 범위 → 자동 행동 0")
    ck(g.ai_proposals_frozen is False and g.suspended_sources == set(), "정상 범위 → 동결/정지 없음")
    ck(g.is_proposal_allowed("ai:gemini") is True, "정상 범위 → 제안 허용")

    # 2) ai_node_ratio 초과 → freeze 자동 발동
    dirty = {
        "total_nodes": 100, "ai_nodes": 50,        # 0.50 > 0.35
        "total_edges": 100, "ai_edges": 10,
        "promotions_total": 100, "promotions_blind_miss": 5,
        "by_source": {},
    }
    g2 = DriftGuard()
    r2 = g2.evaluate(dirty)
    ck("freeze_ai_proposals" in r2["actions_taken"], "ai_node_ratio 초과 → freeze 자동 발동")
    ck(g2.ai_proposals_frozen is True, "동결 상태 진입")
    ck(g2.is_proposal_allowed() is False, "동결 중 → 신규 AI 제안 차단")
    ck(any(b["metric"] == "ai_node_ratio" for b in r2["breaches"]), "위반 항목에 ai_node_ratio 기록")

    # 3) blind_miss_promotion_rate 초과 → freeze
    blind = {
        "total_nodes": 100, "ai_nodes": 5, "total_edges": 100, "ai_edges": 5,
        "promotions_total": 100, "promotions_blind_miss": 30,  # 0.30 > 0.20
        "by_source": {},
    }
    g3 = DriftGuard()
    r3 = g3.evaluate(blind)
    ck("freeze_ai_proposals" in r3["actions_taken"] and g3.ai_proposals_frozen,
       "맹목 승격률 초과 → freeze")

    # 4) source별 철회율 초과 → 해당 source만 suspend (정밀)
    src_bad = {
        "total_nodes": 100, "ai_nodes": 5, "total_edges": 100, "ai_edges": 5,
        "promotions_total": 100, "promotions_blind_miss": 5,
        "by_source": {
            "ai:gemini": {"promoted": 10, "later_deprecated": 1},   # 0.10 ok
            "ai:ollama": {"promoted": 10, "later_deprecated": 5},   # 0.50 > 0.25
        },
    }
    g4 = DriftGuard()
    r4 = g4.evaluate(src_bad)
    ck("ai:ollama" in r4["suspended"], "철회율 높은 source(ollama) 정지")
    ck("ai:gemini" not in r4["suspended"], "정상 source(gemini) 미정지 — 정밀 차단")
    ck(g4.is_proposal_allowed("ai:gemini") is True and g4.is_proposal_allowed("ai:ollama") is False,
       "정지된 source만 제안 거부")

    # 5) 동결/정지는 가역 — unfreeze/resume/reset 으로 복구
    g2.unfreeze_ai_proposals()
    ck(g2.ai_proposals_frozen is False and g2.is_proposal_allowed() is True, "freeze 가역 해제")
    g4.resume_source("ai:ollama")
    ck(g4.is_proposal_allowed("ai:ollama") is True, "suspend 가역 해제")
    g4.reset()
    ck(g4.ai_proposals_frozen is False and g4.suspended_sources == set(), "reset 으로 완전 복구")

    # 6) 행동은 절대 데이터 삭제 0 / L0 불변 (스키마 보장)
    chk = DriftGuard().evaluate(dirty)
    ck(chk["deletes_data"] is False and chk["l0_untouched"] is True and chk["reversible"] is True,
       "자동 행동: 삭제 0 · L0 불변 · 가역")

    # 7) 0/0 = 오염 0 (빈 그래프 안전)
    empty = DriftGuard().evaluate({})
    ck(empty["breaches"] == [] and empty["metrics"]["ai_node_ratio"] == 0.0,
       "빈 입력 → 위반 0 (0/0=0)")

    # 8) 경계값: 임계와 정확히 같으면 미발동(초과 '>' 만 발동 — 결정론)
    boundary = {"total_nodes": 100, "ai_nodes": 35, "total_edges": 1, "ai_edges": 0,
                "promotions_total": 1, "promotions_blind_miss": 0, "by_source": {}}
    rb = DriftGuard().evaluate(boundary)  # 0.35 == 0.35 → 미발동
    ck(rb["actions_taken"] == [], "임계 동일값(0.35==0.35) → 미발동(보수적 '>' 경계)")

    # 9) 멱등: freeze 두 번 호출해도 로그 1건
    gi = DriftGuard()
    gi.freeze_ai_proposals("x"); gi.freeze_ai_proposals("x")
    ck(sum(1 for a in gi.actions_log if a[0] == "freeze_ai_proposals") == 1, "freeze 멱등")

    # 10) ai_edge_ratio 초과 단독 발동
    edge_bad = {"total_nodes": 100, "ai_nodes": 5, "total_edges": 100, "ai_edges": 40,  # 0.40 > 0.30
                "promotions_total": 100, "promotions_blind_miss": 0, "by_source": {}}
    re = DriftGuard().evaluate(edge_bad)
    ck("freeze_ai_proposals" in re["actions_taken"]
       and any(b["metric"] == "ai_edge_ratio" for b in re["breaches"]),
       "ai_edge_ratio 초과 단독 → freeze")

    # 11) threshold 완화 차단 (더 엄격(작게)만 허용 · 큰 값=완화 거부)
    try:
        DriftGuard(thresholds={"ai_node_ratio": 0.99}); ck(False, "임계 완화 차단 실패")
    except ValueError:
        ck(True, "threshold 완화(0.35→0.99) → 거부(더 엄격하게만)")
    # 더 엄격하게(작게)는 허용
    stricter = DriftGuard(thresholds={"ai_node_ratio": 0.10})
    ck(stricter.thresholds["ai_node_ratio"] == 0.10, "threshold 강화(0.35→0.10) → 허용")

    print("\nGATE: %s" % ("GO" if ok else "STOP"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print("usage: python hag_drift_metrics.py --selftest")
    sys.exit(0)
