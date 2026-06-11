# BingguPack Graph Grammar Spec v2.1

> 빙구팩 팩이 따라야 하는 그래프 문법(제품 규격)입니다. 특정 사용자 데이터 전용 스키마가 아니라,
> **어떤 사용자의 데이터든 동일하게 적용되는 규격**입니다. 검증기는 fail-closed로 이 문법을 강제합니다.
> This is the product-level graph grammar every BingguPack pack must follow. Validators enforce it fail-closed.

## 0. 핵심 원칙 (owner grammar)

- **노드**: 단어 노드 금지 · 모든 정보를 노드로 만들지 않음 · **핵심 문장 노드 중심** ·
  기본 5종 = **문서 / 증거 / 개념 / 상태 / 판단** · 세부 노드는 핵심 문장에 종속
- **엣지**: 동사형 ("A는 B 판단의 근거가 된다")
- **증빙**: 근거 없는 연결은 확정 금지 → 검색/판단의 중심은 항상 **evidence index**
- **검증 미달 = 거부가 아니라 보류**(지식은 안 버린다, 근거가 생기면 연결) · **PII/시크릿만 차단**(유일 예외)

## 1. 증빙 모델 — evidence ≠ provenance

- **evidence(증거)** = 원문 근거만: `source_id + 위치(line/anchor) + excerpt_sha`.
  accepted 엣지는 원문 증거 **1개 이상 의무**.
- **provenance(출처표시)** = 파서/frontmatter/폴더 경로 등 시스템 유래 정보 — 증거로 인정하지 않음.
  노드 속성의 별도 필드로만 기록.
- 시스템 정보만 가진 엣지는 accepted 불가.

## 2. 상태 모델 — 영속 2종 + 빌드 재계산

- 영속 저장 상태는 **2개만**: `accepted`(원문 증거 충족) / `blocked`(PII·시크릿 — 비가역 유출 방지).
- `candidate` / `quarantined`는 **매 빌드 재계산되는 산출물** — 사람이 상태를 관리하지 않는다.
- quarantine 항목 필수 3필드: `reason_code` + `fix_condition`(복귀 조건) + `next_check_build`.
  다음 빌드에서 조건 충족 시 **자동 복귀**. quarantine 산출물은 빌드타임 로컬 파일 — hosted 런타임 쓰기 0.

## 3. 노드 5종

| 종 | space | 규칙 |
|---|---|---|
| 판단(Claim) | claim | 핵심 문장(완결문) 1개 = 노드 1개. 문장은 원문 추출/템플릿 **자동 생성** — 사람 관리 필드는 `canonical_name`·`short_label`·`source_ref` 3개만 |
| 개념(Concept) | concept | 고정 목록 금지 — 도출 규칙(메타데이터·네임스페이스·키워드 클러스터)으로 후보 생성. **승인 대상 = 고영향 개념만**(연결 차수 상위 N), 나머지는 규격 통과 시 자동 판정 |
| 상태(State) | state | 현재형 사실만. 과거 기록은 판단으로 |
| 문서(Document) | resource | 마스터 문서만 노드화 ("모든 정보를 노드로 만들지 않음"). 일반 문서는 증거의 출처로만 존재 |
| 증거(Evidence) | evidence | §1 원문 근거. 검색의 1차 표면 |

## 4. 엣지 — predicate registry v1

동사형 술어만 허용:

| relation | 동사 문장 |
|---|---|
| evidence_supports | 이 증거는 이 노드의 근거가 된다 |
| belongs_to | 이 판단은 이 개념에 속한다 |
| recorded_in | 이 판단은 이 문서에 기록되어 있다 |
| supersedes | 이 판단은 저 판단을 대체한다 |
| refers_to | 이 판단은 저 판단을 참조한다 |
| current_state_of | 이 상태는 이 개념의 현재 상태다 |

- registry 추가 = owner 승인만. 미등록 동사는 무한 보류 금지 — 빌드가 자동 처리:
  alias 정규화 / 고영향만 top-N review / 저빈도는 배포 제외 candidate.
- 매 배포 전 신규 predicate top-N 보고서 자동 생성.
- **전 엣지 evidence_refs 의무** — 런타임 로더(`load_packs.ts`)가 노드·엣지 모두 강제 (fail-closed).

## 5. hosted payload 예산

- 팩당 view 캡(20K자)은 불변 — payload에는 `short_label`만 탑재, 전체 문장은 evidence chunk에 보존(`sentence_id` 참조).
- **절단 금지** — 캡 초과 시 빌드 실패 (silently 자르지 않음).
- 구현 전 budget 문서 제출: 노드/엣지 수·평균 label 길이·예상 payload·30% 여유폭.
- 전체 빌드 = **매 배포 반복 게이트**: 증빙 없는 accepted 엣지 0 · 캡 초과 0 · 신규 predicate 보고서 ·
  전 배포 대비 payload +10% 경고 / +20% block.

## 6. 검증기 3단 판정

| 판정 | 대상 | 처리 |
|---|---|---|
| 통과 | 규격 충족 | 그래프 입장 (candidate 신분 — 확정은 사용자만) |
| **보류** | 근거 없음 · 단어 노드 · 비동사 엣지 · 미등록 술어 · 부유 노드 | 버리지 않음 — quarantine 산출물에 사유와 복귀 조건 기록, 다음 빌드 자동 재심사 |
| **차단** | PII·시크릿·유출 패턴 | 마스킹 또는 차단 (비가역 유출 방지 — 유일한 즉시 차단) |

문법 게이트(G7) 검사 항목: 5종 외 node_type 0 · registry 외 relation 0 · 전 엣지 원문 증빙 ·
증거 전건 위치(line+excerpt_sha) 보유 · 단어 노드 0 · 부유 노드 0 · 절단 마커 0.

## 7. 런타임(hosted) 불변

- read-only · candidate-only · promotion_allowed=false — hosted에서 확정 0.
- 런타임에 registry 동적 조회 없음 — 검증은 빌드타임 종결, 클라우드는 frozen graph만 읽는다.
- 런타임 fail-closed = PII 탐지 · 스키마 파손 · manifest 불일치만.
- `evidence_search`는 pack_id 생략 시 전 팩 검색, short_label뿐 아니라 원문 발췌(chunk)도 검색한다.

---

본 스펙은 4-CLI 토론(외부 모델 교차 검토)을 거쳐 확정되었습니다.
레퍼런스 구현(빌더)은 사용자별 데이터 경로에 의존하므로 저장소에 포함하지 않습니다 —
스펙 §1~§7을 만족하는 팩이면 어떤 빌더로 만들어도 동일하게 검증·서빙됩니다.
