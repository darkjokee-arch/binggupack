# BingguPack — 근거 사슬(Rationale Chain) 설계 (DRAFT, 2026-06-14)

> **설계만 — 코드 0.** 구현은 owner 별도 GO 후. 정본(canonical 5종·predicate 매트릭스·evidence 의무·save_selected·preview·graph validation·정규식 classifier) **불변 — 추가만**.
> 4cli 2회 수렴: 근거 사슬은 **신규 predicate를 만들지 않고** 기존 `supports_judgment`(verb "근거가_된다") + 기존 노드 종을 재사용한다(AIF 동형). semantic은 **엣지 후보 추천만**(자동 생성 0·사람 confirm·candidate).

---

## 0. 원칙 (불변)
- 정본 그래프 = canonical 5종 노드(문서·증거·개념·상태·판단) + predicate 매트릭스(`verb_edge_schema.py`) + evidence 원문증빙 의무 + candidate/promotion_allowed=false.
- 근거 사슬은 이 위에 **추가만** — 매트릭스 항목·노드 종·검증 로직 **변경 0**.
- semantic = 추천만. 엣지 자동 생성 0, 자동 저장 0, 최종은 preview→save_selected 사람 게이트.

---

## 1. 문제
사용자 직관: "A는 B의 결정 근거다, B는 C의 교훈 근거다" — 노드를 **근거 관계로 사슬** 연결(Toulmin 논증·design rationale). 1층 분류에서 교훈↔버그패턴이 가까운 것도 **버그패턴이 교훈의 근거**라는 실제 인과 때문(2층으로 흡수). 이 사슬을 정본 무침해로 표현해야 한다.

## 2. 채택안 — AIF 동형: 기존 엣지 재사용 (신규 predicate 0)
실측(`verb_edge_schema.py:21-26`) 기존 매트릭스:
| relation | verb | src | tgt |
|---|---|---|---|
| supports_judgment | 근거가_된다 | 증거·상태·개념 | 판단 |
| contradicts | 반박한다 | 판단·증거 | 판단 |
| depends_on | 선행조건이다 | 판단 | 상태·개념·판단 |
| blocks | 막는다 | 상태·판단 | 판단 |
| enables | 가능하게_한다 | 상태·판단 | 판단 |
| refines | 정밀화한다 | 개념·판단 | 판단·개념 |

- **`supports_judgment`가 이미 "근거가_된다"** = AIF의 "근거(S-node)를 거친 연결"과 동형.
- "A는 B의 결정 근거" = `A(증거/상태/개념) --supports_judgment--> B(판단, semantic_subtype=결정)`. **신규 predicate 0.**
- 근거의 "성격"(결정근거/교훈근거)은 **target 노드의 1층 semantic_subtype**으로 색칠(엣지는 그대로). 표시/검색 보조일 뿐 매트릭스 미참여.

## 3. 판단↔판단 근거 — 중간 근거노드 경유 (가짜 금지)
- 매트릭스상 `supports_judgment`는 **판단→판단 불가**(src에 판단 없음). "결정(판단)이 교훈(판단)의 근거"를 직접 못 그림.
- 해결(AIF 정합): **판단을 직접 잇지 않고**, 그 근거가 된 **실제 증거/상태/개념 노드를 중간에** 둔다.
  - 예: 버그패턴(상태 "오타가 반복된다") --supports_judgment--> 교훈(판단 "보내기 전 확인하자").
  - 판단→판단 정당화는 `depends_on`(선행조건) 등 기존 허용 범위에서만, 또는 중간 근거노드로.
- **금지(4cli C)**: 사슬을 잇기 위한 **가짜 증거노드 양산 금지**. 중간 노드는 **실제 evidence_refs(원문 증빙)가 있을 때만** 생성. 없으면 엣지 추천 보류.

## 4. semantic 역할 = 엣지 후보 추천만
- semantic이 노드 쌍을 보고 "이건 근거 관계일 수 있음"을 **후보로 제시**(점수+근거). 
- 자동 엣지 생성 0. 사람이 preview에서 확인 → save_selected 게이트 → candidate 엣지(promotion_allowed=false).
- 추천 충돌 시 기존 매트릭스/검증 우선(매트릭스 위반 추천은 자동 폐기).

## 5. 안전 — dedup·서브인덱스·회귀 (4cli C·D 선결)
- **evidence dedup 키**(C): 동일 evidence 다중 참조로 노드/엣지 폭증 → dedup 키(evidence_id 또는 text_sha)로 중복 차단. 같은 근거는 하나의 노드 재사용.
- **서브인덱스 격리**(D): 근거 사슬 질의는 메인 그래프와 분리된 보조 인덱스/뷰로 — 기존 pack 질의 지연·결과 영향 0.
- **회귀테스트 선행**(C): 근거 사슬 도입 후 기존 pack의 canonical/predicate/evidence_refs/질의 결과가 **한 건도 변형 0**임을 selftest로 입증(silent 변질 차단).
- 1층 semantic_subtype·2층 추천 모두 graph validation 입력 아님(표시/검색 보조).

## 6. selftest 설계 (temp 전용·정본 무변경)
- 기존 매트릭스 src/tgt 제약 그대로 통과/거부(supports_judgment 판단→판단 거부 등 회귀).
- 중간 근거노드 없는 판단→판단 근거 추천 = 보류(엣지 0).
- evidence_refs 없는 근거노드 생성 시도 = reject(가짜 금지).
- dedup: 동일 evidence 2회 → 노드 1개.
- 추천 자동생성 0(사람 confirm 없으면 엣지 write 0).
- 기존 pack read 회귀: 도입 전후 질의 결과 동일.

## 7. 바꿀 파일 (추가만 — 정본 무변경)
### 신규
- `scripts/binggu_rationale_suggest.py` — 노드 쌍 근거 후보 추천(매트릭스 검증 위임·추천만) + selftest
- `docs/BINGGUPACK_RATIONALE_EDGE_DESIGN.md` (본 문서)
### 무변경 (절대 안 건드림)
- `verb_edge_schema.py`(매트릭스·verb·src/tgt) · `openbinggu_promotion_preview.py`(VERB_MAP) · canonical 5종 · evidence 의무 · save_selected · graph validation · 정규식 classifier.

## 8. 미해결 / owner 결정
- "근거 후보 추천"의 신호: 1층 임베딩 유사도만으로 근거 관계 판정은 약함 → 임계/사람 검토 비율 owner 결정.
- 서브인덱스 물리 형식(뷰 vs 별도 테이블) — 구현 단계 설계.
- 판단↔판단 근거가 실사용에서 얼마나 빈번한지(중간 근거노드로 충분한지) 실측 필요.

**상태:** 설계 방향 정리 완료. 구현·스키마 상세는 별도 GO. 정본 전부 불변·추가만·추천만·자동생성 0.
