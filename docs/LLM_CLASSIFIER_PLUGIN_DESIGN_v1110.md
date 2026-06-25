# LLM Classifier Plugin 설계 (v1.11.0 — STUB ONLY)

> **상태(v1.11.0):** 인터페이스/타입/docstring 만 정의한 **stub**. 실제 LLM 구현 0 · import-time 외부패키지 0 · 네트워크 0.
> 실구현은 **v1.12 별도 GO** 대상. 본 문서는 그 계약(contract)·안전 불변식을 못 박는다.
>
> 코드 정본: `binggupack/classifier/__init__.py` (`ClassifierAdvice`, `AdvisoryClassifier` Protocol).
> 상위 설계 맥락: `docs/BINGGUPACK_SEMANTIC_CLASSIFIER_DESIGN.md` §12~14 (canonical 비침해·계층 분리).

---

## 0. 한 줄 요약

**regex 가 정본, LLM 은 보조(advisory), 실패는 regex fallback.** LLM 의 confidence 는 저장 게이트에 절대 닿지 못한다(타입 경계로 강제).

---

## 1. 역할 분리 — regex 정본 + advisory supplement + regex fallback

| 층 | 모듈 | 결정권 | 변경 |
|---|---|---|---|
| **정본(canonical)** label_kind | `scripts/openbinggu_label_kind_map.classify_label_kind` (5종 정규식) | 최종 | **불변(미접촉)** |
| **정본(canonical)** should_capture | `scripts/binggu_capture_classifier.classify` (규칙 게이트) | 최종 | **불변(미접촉)** |
| **보조(supplement)** | `AdvisoryClassifier` (로컬 LLM) → `ClassifierAdvice` | 추천만 | 신규(v1.12 구현) |

- **정본 우선·최종권**: 두 정규식 모듈이 항상 먼저 돌고, 최종 라벨·저장가치 판정은 정규식이 가진다.
- **LLM = supplement**: `ClassifierAdvice`(label 후보 + confidence + reason)는 **참고 추천**일 뿐, 정규식 판정을 override 하지 않는다.
- **regex fallback(닫힘 구조)**: 모델 없음/호출 실패/타임아웃/JSON schema 위반 → `classify()` 는 `None` 반환 → 파이프라인은 정규식만으로 그대로 동작. LLM 부재로 capture 가 멈추는 일은 없다.

---

## 2. v1.11 범위 — stub 만, optional-extra 조차 금지

- v1.11 산출물 = **타입 + Protocol + docstring + 본 설계문서**. 끝.
- **실 LLM 구현 금지** — Ollama/HTTP 클라이언트·임베딩·프롬프트·schema 검증 로직 일체 v1.11 에 넣지 않는다.
- **optional-extra(pyproject `[project.optional-dependencies]`) 조차 v1.11 엔 금지** — extra 로라도 외부 패키지 진입로를 열지 않는다. v1.11 의 불변식(stdlib-only·import-time 외부패키지 0)을 흠집 없이 유지하기 위함. 실제 의존성·extra 설계는 v1.12 GO 시점에 별도 결정.
- 검증(import 성공 + 외부의존 0)이 통과해야 stage0 DONE.

---

## 3. 보안 불변식 (v1.12 구현체가 반드시 지킬 계약)

> 아래는 stub 단계에서 **문서·docstring 으로 못 박는** 계약이다. v1.12 구현은 전부 준수해야 하며, 위반 시 영구 BLOCK.

### 3-1. network 0 · localhost 강제
- LLM 호출은 **localhost(127.0.0.1) 전용**. 외부 네트워크 호출 0(프라이버시 hard 요구).
- **auto-download 금지** — 모델 자동 pull/fetch 금지. 모델 부재 = advice None(regex fallback), 다운로드 시도 0.
- **base_url override 금지** — 환경변수/설정으로 endpoint 를 외부 주소로 바꾸는 경로 차단(데이터 유출구 봉쇄). 호스트는 127.0.0.1 로 고정.

### 3-2. redaction-before-LLM
- PII/secret 은 **LLM 입력 직전 정규식으로 제거**(`scan_residual_pii`·`SECRET_PATTERNS`·`_PREVIEW_PII_EXTRA` 재적용). hit 이면 LLM 호출 skip → regex fallback + leak_guard 카운터.
- 원문이 LLM·로그·예외·디스크 어디에도 남지 않는다(원문 저장 0).

### 3-3. JSON schema 검증
- LLM 산출은 고정 schema(label ∈ canonical 5종, confidence ∈ [0,1], reason: str)로 **검증**.
- schema 위반/JSON 파싱 실패 → 무조건 advice None(regex fallback) + 위반 카운터. malformed 출력이 파이프라인에 흘러들지 않는다.

### 3-4. LLM confidence 는 저장/고정/미리보기 결정에 미접근 (타입 경계)
- 저장 게이트 시그니처 = `binggu_save_gate.gate_human_for(sentences, path=None, now=None)` — **confidence 인자 없음**.
- 따라서 `ClassifierAdvice.confidence` 는 어떤 경로로도 `gate_human_for` / `gate_record` / `save_selected` 인자가 될 수 없다. LLM 신뢰도가 높아도 사람 SAVE 게이트를 건너뛸 수 없다(불변식 3·1).
- 동일하게 confidence/advice 는 **pinned**(명시 저장 고정)·**preview-skip**(미리보기 스킵) 결정에도 미접근. 이들은 정규식 게이트 전용 필드다.
- → LLM 은 "라벨 후보 + 이유"를 advisory 로 제시할 뿐, **저장 여부·고정·미리보기 스킵에 일절 개입하지 못한다.**

### 3-5. 무상태(stateless) — prev_turn 1턴
- `classify(utterance, prev_turn=None)` 는 **직전 1턴만** 참조. 대화 이력 누적·세션 상태 보존 0.
- 모호 질문 보조 용도로만 prev_turn 사용(기존 규칙 게이트의 무상태 1턴 정책과 정합).

---

## 4. 인터페이스 (코드 정본 요약)

```python
# binggupack/classifier/__init__.py
class ClassifierAdvice:
    label: str        # 추천 label_kind 후보(canonical 5종 권장) — override 아님
    confidence: float # 0~1, 표시/정렬 전용 — save_gate 에 닿지 못함
    reason: str       # 사람이 읽는 한 줄 근거(redaction 이후)

class AdvisoryClassifier(Protocol):
    def classify(self, utterance: str, prev_turn: Optional[str] = None) -> Optional[ClassifierAdvice]:
        ...   # None = regex fallback
```

- `runtime_checkable` Protocol — stdlib `typing` 만 사용. import-time 외부 의존 0.
- 구현체는 이 Protocol 을 만족(structural typing)하면 됨. v1.11 엔 구현체 미포함.

---

## 5. 정본 비침해 확약

- `scripts/openbinggu_label_kind_map.py`(regex 5종)·`scripts/binggu_capture_classifier.py`(규칙 게이트) **한 줄도 수정 안 함** — 본 stub 은 읽기만 했다.
- 노드 = canonical 5종 불변 · 엣지 = 동사 6종 불변 · 저장 게이트(`gate_human_for`·`save_selected`) 불변.
- LLM advice 는 ledger schema 에 들어가지 않는다(저장 0). 표시/추천 레이어일 뿐.

---

## 6. 다음 단계(v1.12 별도 GO)

stub → 구현 전환 시: ① localhost LLM 클라이언트(auto-download·base_url override 금지) ② redaction-before-LLM wrapper ③ JSON schema 검증기 ④ leak_guard 단일 경유 ⑤ 단일 selftest(leak scan + 정규식 byte-diff + advice→gate 미접근 어서션 + fallback). 의존성/optional-extra 설계도 이때 결정. canonical 5종·게이트·정규식 전부 불변 유지.
