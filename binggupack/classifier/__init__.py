"""classifier — Local LLM advisory classifier plugin (v1.11.0 STUB ONLY).

이 패키지는 **인터페이스/타입/docstring 만** 정의한다. 실제 LLM 구현은 v1.12 별도 GO.
v1.11 에서는 import-time 외부 패키지 0 · 네트워크 0 · stdlib-only 를 보장하는 껍데기만 둔다.

설계 정본: docs/LLM_CLASSIFIER_PLUGIN_DESIGN_v1110.md

────────────────────────────────────────────────────────────────────────
정본 vs 보조 (절대 불변)
────────────────────────────────────────────────────────────────────────
- **regex 가 정본(canonical)** 이다:
    binggupack.classifier.label_kind_map.classify_label_kind  (label_kind 5종;
      scripts/openbinggu_label_kind_map.py 는 backward-compatible wrapper, v1.16 strangler Phase2 이관)
    binggupack.classifier.capture_classifier.classify      (should_capture 게이트;
      scripts/binggu_capture_classifier.py 는 backward-compatible wrapper, v1.11.0 phase4 이관)
  이 두 정규식 모듈은 항상 먼저·항상 최종 결정권을 가진다.
- **LLM 은 supplement(보조)** 일 뿐이다. AdvisoryClassifier 가 내놓는 것은
  `ClassifierAdvice` = "참고용 추천"이며, regex 의 판정을 override 하지 못한다.
- LLM 호출 실패/모델 없음/타임아웃 → **regex fallback** 으로 닫힌다(advice=None).
  capture 파이프라인은 LLM 이 없어도 정규식만으로 그대로 동작해야 한다.

────────────────────────────────────────────────────────────────────────
타입 경계 — ClassifierAdvice 는 save_gate 에 닿을 수 없다 (안전 강제)
────────────────────────────────────────────────────────────────────────
저장 게이트 함수의 시그니처는:

    scripts.binggu_save_gate.gate_human_for(sentences, path=None, now=None) -> bool

즉 게이트는 **confidence 인자를 받지 않는다**. ClassifierAdvice.confidence 는
어떤 경로로도 gate_human_for / gate_record / save_selected 의 인자가 될 수 없다.
LLM 신뢰도가 높다고 해서 사람 SAVE 게이트를 건너뛰거나 통과시키는 일은 타입
수준에서 불가능하다 — advice 는 게이트 함수의 어떤 파라미터에도 매핑되지 않는다.
(불변식 3: human confirmation. 불변식 1: no autosave.)

마찬가지로 ClassifierAdvice 는 다음에도 접근/영향을 줄 수 없다:
  - gate_human_for(저장 게이트)        : conf 인자 없음 → 닿지 못함
  - pinned / preview-skip / gate_human  : 정규식 게이트 전용 필드 → LLM 미접근

→ LLM 은 "라벨 후보 + 이유"를 advisory 로 제시할 뿐, 저장 여부·고정·미리보기
  스킵 결정에는 일절 개입하지 못한다.
"""

from __future__ import annotations

from typing import Optional, Protocol, Tuple, runtime_checkable

# regex 정본(should_capture 게이트) — v1.11.0 phase4 이관. 아래 advisory(LLM 보조)와 별개·우선.
from .capture_classifier import classify  # noqa: F401,E402

__all__ = [
    "classify",
    "CANONICAL_LABEL_KINDS",
    "ClassifierAdvice",
    "AdvisoryClassifier",
]

# canonical label_kind 정본(읽기 전용 참고용). 정의 정본은 어디까지나
# binggupack.classifier.label_kind_map.KIND_KO. 여기 사본은 advice.label 후보 검증용 힌트일 뿐.
CANONICAL_LABEL_KINDS: Tuple[str, ...] = ("문서", "증거", "개념", "상태", "판단")


class ClassifierAdvice:
    """LLM 분류기의 **참고용(advisory)** 산출물 — 저장 결정권 없음.

    필드:
      label      : str        — 추천 label_kind 후보(canonical 5종 중 하나 권장).
                                 regex 정본을 override 하지 않는 '보조 추천'.
      confidence : float      — 0.0~1.0 추천 신뢰도. **순수 표시/정렬용**.
                                 save_gate 시그니처에 confidence 인자가 없으므로
                                 이 값은 저장 게이트로 흘러갈 수 없다(타입 경계 강제).
      reason     : str        — 사람이 읽는 한 줄 근거(redaction 이후 안전 텍스트).

    불변:
      - advisory only — 이 객체는 regex 정본의 판정을 바꾸지 못한다.
      - confidence 는 gate_human_for / pinned / preview-skip 의 인자가 될 수 없다.
      - LLM 미가동/실패 시 classify() 는 이 객체 대신 None 을 반환(regex fallback).
    """

    __slots__ = ("label", "confidence", "reason")

    def __init__(self, label: str, confidence: float, reason: str) -> None:
        self.label = label
        self.confidence = confidence
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - 표시 전용
        return (
            "ClassifierAdvice(label=%r, confidence=%.3f, reason=%r)"
            % (self.label, self.confidence, self.reason)
        )


@runtime_checkable
class AdvisoryClassifier(Protocol):
    """로컬 LLM 보조 분류기 계약(stdlib Protocol). v1.11 = 인터페이스만, 구현 없음.

    regex 가 정본이고 본 분류기는 supplement(보조)다. 구현체(v1.12)는:
      - localhost 강제 · 네트워크 외부 호출 0 · auto-download 금지 · base_url override 금지
      - redaction-before-LLM(PII/secret 은 LLM 입력 전 제거)
      - 산출 JSON schema 검증, 위반 시 None(regex fallback)
      - 무상태: prev_turn 은 직전 1턴만 참조(이력 누적 금지)
    를 모두 준수해야 한다(설계문서 §참조). 어떤 경우에도 advice 는 save_gate 에 닿지 못한다.
    """

    def classify(
        self, utterance: str, prev_turn: Optional[str] = None
    ) -> Optional[ClassifierAdvice]:
        """발화 1건에 대한 **보조 추천** 반환.

        Args:
          utterance : 분류할 발화(redaction 이후 텍스트 전제).
          prev_turn : 직전 1턴(optional, 무상태 — 이력 누적 안 함).

        Returns:
          ClassifierAdvice  — LLM 보조 추천(저장 결정권 없음).
          None              — 모델 없음/실패/타임아웃/schema 위반 → **regex fallback**.

        구현은 절대 저장(write)·게이트 통과·자동 적재를 수행하지 않는다(advisory only).
        """
        pass
