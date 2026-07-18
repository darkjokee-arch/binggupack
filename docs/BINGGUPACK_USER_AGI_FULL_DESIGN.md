# BingguPack User-AGI Full Design

> ⚠️ **종속 고지 (2026-06-17):** 이 문서는 `BINGGUPACK_CONSTITUTION_2026-06-17.md`(헌법)에 종속된다. 충돌 시 **헌법이 우선**. 아래 조항 중 자동수집·외부수확·자동채움·관계 관련 일부는 헌법 §1·§2·§3·§6으로 대체·갱신되었다.

> 상태: 2026-06-17 기준 전체 설계서.
> 목표는 "사용자가 직접 고른 판단과 why를 모아, 여러 LLM에서 사용자의 판단 습관을 재현하는 개인 AGI 기억층"이다.
> 핵심 원칙은 그대로 유지한다: AI는 추천만, 영구 저장은 사람 도장만.

---

## 1. 한 줄 정의

BingguPack은 LLM 대화에서 나온 사용자의 판단, 의문, 이유, 선호, 실수 패턴을 노드와 엣지로 저장하고, 다음 작업 전에 불러와 반문과 판단 보조에 쓰는 개인 판단 그래프다.

LLM wiki와의 차이는 다음이다.

| 구분 | LLM wiki | BingguPack |
|---|---|---|
| 주 역할 | 지식 문서 저장소 | 사용자 판단 원장 |
| 저장 단위 | 문서/요약/지식 | 사람이 고른 핵심 문장 |
| AI 역할 | 검색/요약 | 후보 추천, 근거 추천, 반문 |
| 최종 권한 | 문서 관리자 또는 모델 | 사용자 SAVE/승인 |
| 목표 | 더 잘 찾기 | 같은 실수 방지, 사용자식 판단 재현 |

---

## 2. 최종 목표

최종형은 "사용자 복제품" 자체가 아니라, 사용자의 판단 체계를 LLM 위에 올리는 실행층이다.

입력이 들어오면 다음 순서로 동작한다.

1. 현재 작업을 읽는다.
2. 관련 노드와 why-edge를 검색한다.
3. 과거 판단, 실수, 선호, 설계결정을 요약한다.
4. 위험하거나 애매하면 먼저 반문한다.
5. 사용자의 기존 판단 방식에 맞는 진행안을 제안한다.
6. 사용자가 새로 남긴 why를 다시 후보로 저장한다.
7. 반복되는 판단은 hook, AGENTS, 테스트 같은 하네스로 승격한다.

즉 BingguPack은 기억 원장이고, 하네스는 실행 강제 장치다.

---

## 3. 현재 구현 기준

> ⚠ 아래 표·edge 절은 2026-06-17 시점 실측 기록. 이후 구현 반영(2026-07): 외부 소스 자동 수확 = scripts/binggu_harvest.py + MCP harvest_add/list/remove로 구현(후보 단계). edge 저장 경로 = binggu.py `cmd_pair`(노드2+엣지1, save_paired) 추가로 사용자 pair 저장 경로 개통. outcome attribution = recall_run_outcomes(binggu outcome CLI) 추가. 표의 '계획(P1)'·'edge 명령 0'은 당시 기준.

현재 이미 있는 축은 다음이다.

| 층 | 구현 상태 | 대표 모듈 |
|---|---|---|
| 자동 후보 수집 | 있음(후보 단계). 영구 저장은 사람 SAVE만 — 헌법 §1·§6 | `binggu_capture_persist.py` |
| 기록 폴더 자동 채움 (need-based) | 계획(P0). 기존 기록(박제·traj·md) → 점·근거·관계 자동 후보 — 헌법 §2·§5 | `watcher_pack_builder` |
| 외부 소스 자동 수확 | 계획(P1). 사람 등록 소스만(arXiv·GitHub·RSS)·후보로만·영구는 사람 SAVE — 헌법 §2·§6 | autopush 스케줄러 |
| 5종 노드 도장 | 있음 | `openbinggu_label_kind_map.py` |
| AI 의미 도장 추천 | 있음. `semantic_label_enabled=True` | `binggu_canonical_semantic.py` |
| semantic_subtype | 있음. ledger와 pack에 전파됨 | `binggu_semantic_subtype_backfill.py`, `binggu_realpack_build.py` |
| why/rationale 추천 | 있음. read-only 후보 | `binggu_rationale_suggest.py` |
| graph preview/confirm | 있음. 저장 0 | `binggu_graph_preview.py`, `binggu_graph_confirm.py` |
| Hybrid-AGI blind 승인 | 있음. temp 실험 파이프라인 | `scripts/hybrid_agi/` |
| cloud read 공유 | 있음. PC-mediated read-only | Cloudflare Worker + KV |

현재 실제 데이터 기준:

- `nodes.semantic_subtype` 존재
- hosted `packs.json`에도 `semantic_subtype` 존재
- `edges/evidence`에는 subtype 없음
- subtype은 노드 보조필드이며 canonical node type이 아니다

관계(edge) ledger 저장 실상태 (2026-06-17 코드 실측):

- `watcher_edge_mvp21`: evidence_supports edge를 **dry-run temp JSONL로만 생성**(운영 INSERT 0).
- `binggu_graph_confirm`: approve해도 **report only**, ledger write 0 (= §14 옛 우선순위가 메우려던 갭).
- `hag_sync_adapter.import_confirmed_edges`: **유일하게 운영 ledger.edges INSERT 작동**. 단 `actor=human` owner-only + owner 31노드 전용 후보(신규 유저 후보 0).
- `binggu.py` CLI: edge 관련 명령 0 (사용자가 손으로 관계를 만들어 저장하는 경로 없음).
- → 결론: 신규/일반 유저가 관계를 ledger에 저장하는 작동 경로 = 0. 그래서 ㉯ 관계저장은 ㉮ 입력으로 점이 쌓인 뒤 `hag_sync_adapter` 경로 확장으로 연다(§14).

---

## 4. 핵심 데이터 모델

### 4-1. Node

노드는 사용자가 저장한 핵심 문장이다. 단어 노드가 아니라 문장 노드가 기본이다.

```text
node_id
node_type          # canonical 5종: doc/evidence/concept/state/judgment
sentence           # 사용자가 고른 문장 전체
semantic_subtype   # 보조 성격: 교훈/결정/선호/설계결정/버그패턴/사실
candidate
promotion_allowed
state
evidence_refs
content_hash
created_at
```

규칙:

- `node_type`은 존재론이다.
- `semantic_subtype`은 "왜 저장했는가/어떤 성격인가"다.
- subtype을 node_type으로 승격하지 않는다.
- 같은 문장이 `node_type=judgment`, `semantic_subtype=교훈`일 수 있다.

### 4-2. Evidence

evidence는 원문 근거다.

```text
evidence_id
sentence
source_pointer_id
source_hash
redaction_policy
pack_id
created_at
```

규칙:

- evidence 없는 근거 엣지는 확정하지 않는다.
- provenance와 evidence를 섞지 않는다.
- secret/PII는 저장 후보에서 제외한다.

### 4-3. Edge

엣지는 노드 사이의 동사형 관계다.

```text
edge_id
relation
source
target
candidate
state
evidence_refs
pack_id
content_hash
created_at
```

why-edge는 새 relation을 만들지 않고 기존 relation을 재사용한다.

핵심은 `supports_judgment`다.

```text
증거/상태/개념 --supports_judgment--> 판단
```

예:

```text
상태: "오타가 반복된다"
  --supports_judgment-->
판단: "보내기 전 확인하자" / semantic_subtype=교훈
```

---

## 5. 계층 구조

### L0. 원문 저장층

사용자가 직접 SAVE한 문장이다.

역할:

- 사용자가 실제로 한 판단을 훼손 없이 보존
- AI가 쪼개거나 고쳐서 원본처럼 저장하지 못하게 막음
- 이후 L1/L2 추론의 근거가 됨

불변:

- 사람 SAVE 없이 영구 저장 0
- 원문 문장 변형 금지
- AI 자동 확정 금지

### L1. 의미/성격층

L0 위에 붙는 분류다.

구성:

- canonical 5종: `doc/evidence/concept/state/judgment`
- semantic_subtype 6종: `교훈/결정/선호/설계결정/버그패턴/사실`

AI는 여기서 추천할 수 있다.

허용:

- "이 문장은 판단 같다"
- "이 문장은 교훈 같다"
- "이 문장은 버그패턴 같다"

금지:

- 추천 결과만으로 저장 확정
- subtype으로 canonical node_type 변경
- confidence 점수로 SAVE 대체

### L2. Why-edge층

노드 사이에 "왜" 관계를 연결한다.

역할:

- "이 상태가 저 교훈의 근거다"
- "이 증거가 저 결정의 근거다"
- "이 개념이 저 판단의 전제다"

AI는 edge 후보를 추천한다.

확정은 사람 confirm 이후에만 가능하다.

### L3. Graph 검증층

추천된 노드와 엣지가 그래프 문법을 지키는지 검증한다.

검증:

- node_type 5종 유지
- relation 허용 목록 유지
- source/target 매트릭스 통과
- evidence_refs 필수
- dangling edge 차단
- self-loop 차단
- subtype canonical 승격 warning/block

### L4. 검색/회상층

질문이 들어오면 그래프에서 관련 기억을 꺼낸다.

질문 예:

- "내가 왜 이 방식을 싫어했지?"
- "이전에도 비슷한 실수 있었나?"
- "이 결정 근거가 뭐였지?"
- "이 작업 전에 내가 항상 체크하라고 한 게 뭐였지?"

반환은 문서 검색 결과가 아니라 판단 사슬이어야 한다.

```text
현재 작업
→ 관련 버그패턴
→ 그때 만든 교훈
→ 사용자가 선호한 처리 방식
→ 이번 작업에서 먼저 물어봐야 할 질문
```

### L5. Preflight층

LLM이 작업을 시작하기 전에 관련 기억을 먼저 읽는 층이다.

입력:

- 현재 cwd
- 사용자 요청
- 작업 종류
- 파일/도메인 힌트

출력:

- 관련 판단 3~7개
- 관련 why-edge 3~7개
- 위험 패턴
- 반문 필요 여부
- 이번 작업 행동 지침

여기부터 BingguPack은 단순 검색이 아니라 작업 전 판단 보조가 된다.

### L6. 반문 엔진

사용자가 "이상한데?", "왜 이렇게 하지?", "이게 맞나?"라고 말하면 그것 자체가 why 신호다.

처리:

1. 사용자 의문 문장을 후보 노드로 잡는다.
2. 해당 의문이 향하는 대상 노드를 찾는다.
3. 답변이나 증거를 연결 후보로 만든다.
4. 사람이 저장하면 why-edge로 확정한다.

예:

```text
사용자: "여긴 왜 이렇게 해서 진행하는 거지?"
→ why-question 후보
답변: "기존 구조가 cloud read-only라 로컬 ledger를 원본으로 둔 것이다"
→ evidence/판단 후보
edge: 답변 --supports_judgment--> 기존 결정
```

### L7. 하네스층

반복되는 판단은 단순 기억으로 두면 강제력이 없다.

하네스 승격 대상:

- 같은 실수 3회 이상
- 고위험 운영 규칙
- secret/DB/배포 관련 금지선
- 사용자가 반복해서 고친 응답 습관
- 작업 시작 전 반드시 확인해야 하는 절차

승격 위치:

- `AGENTS.md`
- Codex/Claude custom instruction
- hook
- preflight script
- test
- MCP tool guard

규칙:

- BingguPack 원장은 근거를 보관한다.
- 하네스는 실행을 강제한다.
- 원장과 하네스를 섞지 않는다.

---

## 6. AI 자동 추천 시스템과의 관계

현재 BingguPack에는 이미 AI/추론 추천 시스템이 있다.

따라서 새로 따로 만들지 않고 이 라인을 이어야 한다.

### 6-1. 노드 후보 추천

대화에서 저장할 만한 문장을 후보로 뽑는다.

상태:

- preview/candidate only
- 저장 0
- 사람 SAVE 필요

### 6-2. canonical label 추천

`binggu_canonical_semantic.py`가 embedding 기반으로 5종 도장을 제안한다.

상태:

- opt-in 또는 Ollama bge-m3 감지 기반
- `semantic_label_enabled=True`
- 추천만

### 6-3. semantic_subtype 추천

`binggu_semantic_shadow.py`가 subtype을 추천한다.

상태:

- subtype은 보조필드
- should_capture/confirm 결정에 쓰지 않음
- secret/PII 선차단

### 6-4. rationale/edge 추천

`binggu_rationale_suggest.py`가 why와 edge 후보를 만든다.

상태:

- 자동 저장 0
- evidence 없는 edge 보류
- 신규 predicate 0
- hallucinated node 0

### 6-5. graph preview/confirm

`binggu_graph_preview.py`와 `binggu_graph_confirm.py`가 edge 후보를 검증하고 승인 화면을 만든다.

상태:

- report only
- 사람 승인 전 pack/DB write 0
- invalid edge approve 차단

### 6-6. Hybrid-AGI

`scripts/hybrid_agi/`는 AI가 새 명제나 추론 엣지를 만들 수 있는 실험층이다.

원칙:

- AI 제안은 권위 0
- 사람이 먼저 답하고
- 그 뒤 AI 제안을 공개하고
- 사람 도장 후에만 영구화

이것은 "AI가 없는 연결을 만들어내는 능력"과 "사용자 정체성 보존" 사이의 절충안이다.

---

## 7. 전체 동작 흐름

### 7-1. 일반 SAVE 흐름

```text
대화/작업 중 사용자 발화
→ capture 후보 생성
→ label_kind 추천
→ semantic_subtype 추천
→ preview 표시
→ 사용자 SAVE n
→ save_selected 재실행
→ PII/secret/A0 재검증
→ ledger.nodes/evidence/evidence_supports 저장
→ pack build
→ cloud read-only publish
```

### 7-2. why-edge 흐름

```text
저장된 노드 또는 preview 후보
→ subtype과 node_type 확인
→ rationale 추천
→ edge 후보 생성
→ graph preview
→ matrix/evidence 검증
→ 사용자 approve/reject/defer
→ 승인 edge만 ledger 저장
→ pack build
```

### 7-3. 사용자 why 발화 흐름

```text
사용자 자연어 의문
→ why-question 후보
→ 관련 대상 노드 검색
→ 답변/증거 후보 생성
→ supports_judgment/refines/depends_on 후보 생성
→ 사람 SAVE/approve
→ why-edge 확정
```

### 7-4. 작업 전 preflight 흐름

```text
사용자 요청
→ 요청 키워드/도메인 추출
→ 관련 node/subtype/edge 검색
→ 위험 패턴 감지
→ 반문 필요 여부 판단
→ LLM 시스템 컨텍스트 상단에 짧게 주입
→ 작업 수행
→ 새 교훈/why 후보 수집
```

### 7-5. 하네스 승격 흐름

```text
why-edge 누적
→ 반복 패턴 감지
→ 위험도 평가
→ 사용자 승인
→ AGENTS/hook/test/custom instruction 반영
→ 이후 작업에서 강제 적용
```

---

## 8. 저장과 강제성의 경계

BingguPack에 저장했다고 해서 LLM이 무조건 따르는 것은 아니다.

강제성은 아래 단계로 갈수록 강해진다.

| 단계 | 강제력 | 설명 |
|---|---:|---|
| pack 저장 | 낮음 | 읽을 수는 있지만 안 읽으면 효과 없음 |
| MCP 검색 | 중간 | LLM이 도구를 호출하면 반영 가능 |
| preflight 자동 주입 | 높음 | 작업 전 관련 기억을 상단에 넣음 |
| AGENTS/custom instruction | 높음 | 응답 방식에 지속 반영 |
| hook/test/guard | 매우 높음 | 위반 시 실행 차단 가능 |

따라서 최종형은 pack만으로 끝나지 않는다.

정답 구조:

```text
BingguPack = 기억 원장
Preflight = 기억 호출
Harness = 반복 실수 강제 방지
```

---

## 9. 다음 구현 단계

> ⚠️ **순서 주의 (2026-06-17):** 아래 Phase 1~10은 기능 축의 목록이며 **owner 환경 기준 구상**이다. 실제 착수 순서는 §14(공개배포 빈 뼈대 전제)가 우선한다 — 첫 삽은 **㉮ 입력 범용화(`watcher_incoming_folder_adapter`)**, 그다음 온보딩, 그다음에야 Phase 2(why-edge 저장)다. 점이 0개인 신규 유저에겐 입력이 선행 조건이다.

### Phase 1. subtype 품질 고정

목표:

- 모든 active node에 subtype이 있음
- 틀린 subtype을 수정할 수 있음
- subtype 분포가 한쪽으로 쏠리지 않음

필요 기능:

- `subtype review`
- `subtype set <node_id> <subtype>`
- `subtype audit`

검증:

- NULL subtype 0
- canonical node_type 불변
- pack export에 subtype 전파

### Phase 2. why-edge 저장 경로 연결

목표:

- 현재 read-only graph confirm 결과를 실제 ledger edge 저장으로 연결

필요 기능:

- edge approval command
- approved edge insert
- duplicate edge 방지
- evidence_refs 검증
- audit log

금지:

- AI 추천 edge 자동 저장
- evidence 없는 edge 저장
- 판단→판단 `supports_judgment` 직접 저장

### Phase 3. 사용자 why 발화 캡처

목표:

- 사용자의 자연어 의문을 why 신호로 잡기

후보 패턴:

- "왜"
- "이상한데"
- "이게 맞나"
- "여긴 왜 이렇게"
- "무슨 근거로"
- "왜 이 방식"

출력:

- why-question candidate
- target candidate
- answer/evidence candidate
- edge proposal

### Phase 4. why 검색 API

목표:

- LLM이 사용자 판단 근거를 쉽게 찾게 함

도구 예:

```text
why_search(query, limit)
judgment_trace(node_id)
similar_mistakes(query)
preference_lookup(domain)
risk_patterns(query)
```

응답 형식:

```text
relevant_nodes
relevant_edges
evidence
summary
recommended_question
confidence
```

### Phase 5. preflight 주입  — 자동주입 경로 구현됨(2026-06-18)

목표:

- 작업 시작 전 관련 기억을 자동으로 불러오기

입력:

- user prompt
- cwd
- files changed
- domain hints

출력:

- "이번 작업 전에 기억할 것"
- "반문해야 할 것"
- "하면 안 되는 과거 패턴"
- "사용자 선호"

구현 상태(2026-06-18):

- 회상 엔진 `scripts/binggu_recall.py preflight_context()` — read-only(mode=ro · ledger write 0).
- **자동주입 경로** `hooks/binggu_preflight_hook.py` (UserPromptSubmit hook):
  작업 발화 → preflight_context → 관련 기억/위험패턴/선호/반문을 **대화 상단에 stdout 으로 주입**(정보 표시만).
  - 기본 OFF(`~/.binggupack/preflight_enabled` 플래그 필요 — 타 세션 무부담).
  - 차단 0(항상 exit 0) · 저장 0 · 무관 작업이면 주입 0(소음 0) · 신규 사용자 graceful.
  - 설치/토글: `python binggu.py preflight --install / --enable / --disable / --uninstall / --auto-status`.
- 헌법 절대제약 준수: 영구=사람 SAVE 만 · AI 추천(정보)만 · 무승인 자동적용 0 · 직감검열 0 ·
  외부수확 없음(local ledger 만) · node→node 강한관계 자동생성 0 · 운영 ledger 무단 write 금지(read-only) · cloud 무관.

### Phase 6. 반문 엔진

목표:

- 과거 위험 패턴과 닮으면 바로 질문

예:

```text
이 작업은 과거에 "검증 없이 바로 배포"해서 실패한 패턴과 비슷합니다.
먼저 로컬 selftest와 live endpoint 확인까지 하고 진행할까요?
```

규칙:

- 위험도 낮으면 조용히 참고
- 위험도 중간이면 짧게 경고
- 위험도 높으면 반문 후 진행

### Phase 7. 하네스 승격

목표:

- 반복되는 why-edge를 실행 규칙으로 올림

승격 조건:

- 동일 실수 3회 이상
- 고위험 작업
- 사용자가 명시한 운영 금지선
- 반복 반문으로 해결된 패턴

출력:

- AGENTS rule
- hook
- test
- checklist

### Phase 8. Hybrid-AGI 통합

목표:

- AI가 없는 명제/엣지를 제안할 수 있게 하되, 사람 blind 승인으로만 영구화

적용 대상:

- 숨은 전제 추출
- 충돌 판단 발견
- 오래된 판단 갱신 제안
- 판단 사슬 중 빠진 중간 근거 제안

불변:

- AI 제안은 candidate
- 사람 답이 먼저
- reveal 이후 도장
- copy suspected면 차단

### Phase 9. 멀티 LLM 공유

목표:

- Claude, Codex, ChatGPT, Gemini가 같은 read-only pack을 읽음

구조:

```text
PC local ledger = 원본
Cloudflare KV pack = read-only 복제
MCP server = 검색/조회 표면
각 LLM = preflight/harness 별도 연결
```

주의:

- Cloud를 원본으로 승격하지 않음
- remote write는 별도 설계 전까지 금지
- token/endpoint 혼동 방지

### Phase 10. 최종 운영 루프

목표:

- 사용자가 아무 생각 없이 써도 과거 판단이 계속 작동

루프:

```text
작업 시작
→ preflight 기억 호출
→ LLM 판단 보정
→ 위험 시 반문
→ 작업 수행
→ 새 why 후보 생성
→ 사용자 SAVE/approve
→ pack publish
→ 반복 패턴은 하네스 승격
```

---

## 10. MCP/API 설계

### 10-1. 읽기 도구

```text
pack_list
pack_summary
node_get
evidence_search
why_search
judgment_trace
preflight_context
```

읽기 도구는 cloud read-only에서 가능하다.

### 10-2. 로컬 쓰기 도구

```text
capture_preview
candidate_save
subtype_set
edge_preview
edge_confirm
why_capture
```

쓰기 도구는 로컬 PC 원본에서만 허용한다.

### 10-3. 금지

```text
remote_auto_save
cloud_write_as_origin
ai_confirm
ai_promote
```

> (헌법 §3·§6) `dynamic_registry_sync`는 금지 목록에서 제외. 외부 소스 수확·자동 관계 생성은 허용되되, **① 사람이 등록한 소스만 ② 후보로만 ③ 영구는 사람 SAVE** 3중 게이트를 통과해야 영구화된다. 금지되는 것은 "통제 없는 자동 영구화"뿐이다.

---

## 11. 검증 기준

모든 단계는 selftest를 가진다.

필수 검증:

- **무승인** 자동 저장 0 (사람 SAVE/승인 후 저장만 허용. 자동 수집·수확·채움은 후보 단계까지만 — 헌법 §1·§6)
- 자동 생성 관계 근거 필수 + 키에 근거·작성자 포함(조용한 덮어쓰기 방지 — 헌법 §6)
- 외부 수확 source 화이트리스트 검증 (사람 등록 소스만 — 헌법 §6)
- 사람 승인 없는 active/confirmed 0
- secret/PII 저장 0
- evidence 없는 edge 확정 0
- subtype canonical 승격 0
- relation registry 외 edge 0
- source/target 매트릭스 위반 0
- cloud read-only write 0
- 운영 ledger 미접촉 selftest는 temp에서만 수행

권장 검증:

- duplicate edge dedup
- payload budget
- live MCP 조회
- pack export 전후 count 비교
- preflight 응답 길이 제한

---

## 12. 위험과 방어책

| 위험 | 설명 | 방어 |
|---|---|---|
| AI가 사용자 판단을 오염 | AI 제안이 사용자 생각처럼 저장됨 | 사람 SAVE, blind commit-reveal |
| subtype 오분류 | 잘못된 성격 태그가 검색을 흐림 | review/set/audit |
| edge 폭증 | 모든 노드를 서로 연결 | evidence dedup, top-N, token guard |
| 가짜 근거 | AI가 그럴듯한 근거 생성 | evidence_refs 필수, hallucinated node 0 |
| 강제성 착각 | pack에 저장만 하고 안 읽음 | preflight/harness 분리 |
| cloud 원본화 | remote write가 원본을 오염 | PC local ledger 단일 원본 |
| 하네스 과잉 | 너무 많은 규칙으로 작업 방해 | 반복 3회/고위험만 승격 |

---

## 13. 최종 완성 판정

완성은 기능 수가 아니라 루프가 닫혔는지로 판단한다.

완성 조건:

- 사용자의 저장 판단이 모든 LLM에서 조회된다.
- 작업 전 관련 기억이 자동으로 들어온다.
- 과거 실수와 닮으면 LLM이 먼저 반문한다.
- 사용자의 why 발화가 노드/엣지 후보로 잡힌다.
- 사람이 승인한 why-edge만 영구화된다.
- 반복 패턴은 하네스로 승격된다.
- 틀린 기억은 supersede/deprecate로 교정된다.
- cloud는 read-only 복제이고 PC ledger가 원본이다.

최종 상태:

```text
사용자 판단 원장
  + why-edge
  + preflight retrieval
  + 반문 엔진
  + 하네스 승격
  + 멀티 LLM read 공유
= 사용자식 판단을 재현하는 개인 AGI memory layer
```

---

## 14. 바로 다음 작업 (2026-06-17 4cli 토론으로 순서 재확정)

> ⚠️ **갱신 사유:** 아래 "graph_confirm → ledger edge 저장" 중심의 옛 우선순위는 **owner 31노드 환경 전제**(점이 이미 많아 선만 이으면 되는 상태)였다. 빙구팩은 **공개배포 빈 뼈대**이고, 신규 사용자는 점이 0개라 관계저장부터 만들면 화면이 영원히 빈칸이다(코드 실측: `binggu_graph_preview` 빈 입력 후보 0). 따라서 첫 삽은 **㉮ 입력 범용화**로 점·근거를 먼저 채우는 것이다. (debate: `~/.claude/memory/debate/20260617_1949_binggupack_p0_3branch/`)

### P0 3갈래와 확정 순서
P0(헌법 §5) = 기존 기록 → 점·근거·관계 자동 채움. 세 갈래의 순서:

1. **㉮ 입력 범용화 (첫 삽)** — 박제·traj·md 폴더를 읽어 evidence·노드를 자동 채움. 이게 곧 신규 온보딩(자기 기록 먹이기).
2. **㉰ 빈그래프 온보딩** — "폴더 넣기 → 점 생성" 흐름 노출 + 빈 장부가 정상임을 안내.
3. **㉯ 관계 실제저장** — 점이 쌓인 뒤. 새 `EDGE SAVE` 경로를 만들지 말고 기존 `hag_sync_adapter.import_confirmed_edges(actor="human")`(멱등·dangling skip 완성)를 확장 재사용. (별도 경로 신설 시 ledger write 두 갈래로 갈려 데카르트곱 발산을 두 곳에서 막아야 함)

### 첫 구현 모듈 — `watcher_incoming_folder_adapter.py` (신규)
- 입력: 박제·traj·md 폴더 (`source_path`/`mtime`/`source_kind`)
- 출력: 기존 MVP2 노드 파이프라인 호환 `evidence_chunk[]` (각 chunk: `evc_id`=`sha256[:24]`, `source_path`, `block_index`, `text`, `text_hash`). PII/secret 잔존 시 전체 STOP.
- 핵심함수: `scan_markdown_files` / `parse_markdown_preserve_blocks`(표·코드블록·중첩리스트를 쪼개지 않고 한 덩어리 보존 — `adapt_text`의 빈줄 단락분할 우회) / `make_evidence_chunks` / `make_evc_id`(기존 `EVC-sha8` 폐기, 대량 박제 충돌 방지) / `redact_and_validate`(기존 `batch_redact`·`scan_residual_pii` 재사용) / `adapt_incoming_folder`
- **P0 제외(나중)**: manifest 증분, quarantine, owner ledger 통합, 공개배포용 명칭 일반화.

### 검증 게이트 (직접실행, mock만으론 GO 금지)
실 박제 골든셋 20개+ / 근거 1:1 추적률 100%(output→source_path+block 역추적) / dedup 충돌 0 / redaction false negative 0 / PII잔존 시 전체 STOP / 기존 MVP2 노드 생성까지 실제 연결 / 표·코드블록·중첩리스트 구조보존 샘플 확인.

### ㉯ 관계저장 단계의 옛 정의 (점이 쌓인 뒤 수행)
```text
approved edge
→ validate_verb_edge 재검증
→ evidence_refs 확인
→ duplicate 확인
→ ledger.edges INSERT  (hag_sync_adapter.import_confirmed_edges 경로 재사용)
→ audit append
→ pack export
→ cloud read-only publish
```
node→node 강한관계(`supports_judgment`) 자동생성은 영구 금지(사람 도장만). 자동은 evidence_supports(증거→판단)뿐.

이 순서로 가면 BingguPack은 "owner 한 명의 노드 저장소"에서 "누구나 자기 기록을 먹여 채우는 판단 그래프"로 넘어간다.

---

## 15. 완성까지 전체 로드맵 (Loopy 장점 10개 흡수 지도)

> **빙구팩 완성 = Loopy의 장점 10개를 빙구팩 안전벨트(사람 승인·근거 필수·로컬 원본)로 감싸 전부 흡수한 상태.** 골격(저장·도장·멀티 LLM read 공유)은 이미 있고, "자동으로 채워지고 꺼내 쓰는 본체"가 단계별로 채워진다.
>
> **순서 원칙: 연료(데이터) → 그래프(점·선) → 머리(꺼내쓰기·판단·자기개선).** 점(데이터)이 없으면 윗단계가 전부 빈 껍데기다 — 랭킹할 게 없고, 회상할 게 없고, 반문할 근거가 없다. 그래서 입력(㉮)이 모든 기능의 연료이자 첫 삽이다.
>
> 헌법 §5 우선순위(P0~P3)와 정합. 각 Stage = 헌법 P단계 + Loopy 흡수 항목 매핑.

### Stage 0 — 골격 (이미 있음 ✅)
- 로컬 장부 저장, 수동 SAVE 라이프사이클, canonical 5종 도장, semantic_subtype, AI 라벨/rationale 추천(후보), 멀티 LLM read-only 공유(폰/웹 — Cloudflare KV).
- 상태: "로컬 개인 판단 장부"로는 지금도 작동. 단 자동 채움·꺼내 쓰기는 비어 있음.

### Stage 1 = P0 — 자동 채움 (현재 착수) ⏳
| 항목 | 내용 | Loopy 흡수 |
|---|---|---|
| ㉮ 입력 범용화 (첫 삽) | 박제·traj·md 폴더 → evidence·노드 자동 채움 (`watcher_incoming_folder_adapter`) | need-based 자동 생성 |
| ㉰ 빈그래프 온보딩 | "폴더 넣기 → 점 생성" 노출 + 빈 장부 정상 안내 | — |
| ㉯ 관계 실제저장 | 점끼리 선(evidence_supports) 저장 (`hag_sync_adapter` 확장) | — |
- **완료 기준**: 내 기록 폴더를 먹이면 점·근거·관계가 후보로 차고, 사람 SAVE로 실제 그래프가 된다. (상세 = §14)

### Stage 2 = P1 — 수집·거르기 (공개배포 release gate 포함)
| 항목 | 내용 | Loopy 흡수 |
|---|---|---|
| 외부 수확 | 사람이 등록한 소스(arXiv·GitHub·RSS) 주기 수집 — 후보로만, 영구는 사람 SAVE | 시간 자동 수확 |
| 철학 필터(열린) | 수확물을 내 가치관으로 거르되 배제 아님 (맞으면 우선순위↑) | Loopy Philosophy Filter |
| keep / challenge / discard | 보관 / 도전 보관(주기적 반문) / 버림(이유 남김) | keep/challenge/discard |
| pack 랭킹 | 신선도+관련성+유용성으로 회상 우선순위 | pack 우선순위 랭킹 |
| README 정직화 | "빈 뼈대, 네가 채운다" — owner 31노드 자랑 제거 (공개 release gate) | — |
- **완료 기준**: 내가 등록한 외부 소스에서도 후보가 차고, 내 가치관에 안 맞는 건 "도전"으로 따로 보관되며, 회상 시 중요한 것부터 나온다.

### Stage 3 = P2 — 꺼내 쓰기 (머리가 켜지는 단계)
| 항목 | 내용 | Loopy 흡수 |
|---|---|---|
| 회상 / preflight | 작업 시작 전 관련 판단·관계를 자동으로 상단 주입 | — (빙구팩 핵심) |
| 반문 엔진 | 위험·애매하면 먼저 "이거 맞아요?" 질문 | — |
| 자기개선 planner | 반복 실수 신호 → 하네스 승격 "후보 제안"(적용은 사람) | 자기개선 planner |
| dry-run / safe apply / 롤백 | 적용 전 안전검사 + 영수증 + 되돌리기 표준화 | dry-run 검증 / safe apply |
- **완료 기준**: 작업 전에 "예전에 이런 실수 했어요"가 자동으로 뜨고, 위험하면 먼저 물어본다.

### Stage 4 = P3 — 강제·확장·진화 (루프 닫힘)
| 항목 | 내용 | Loopy 흡수 |
|---|---|---|
| 하네스 점진 승격 | 반복 패턴 → 경고 → 소프트필수 → 하드게이트 (효과 측정 후 사람 승인) | 점진 승격 + 효과 측정 |
| 철학 진화 루프 | "도전" 보관 항목이 반복 옳다고 판명 → 내 철학 기준 자체를 재검토 (필터 고정 금지) | 철학 진화 루프(신규) |
| 멀티 LLM 공유 완성 | Claude·Codex·ChatGPT·Gemini가 같은 read-only pack을 작업에 활용 | — |
- **완료 기준**: 반복 실수가 자동으로 규칙(hook/test)이 되어 막히고, 내 생각과 다른 근거 있는 의견도 살아남아 내 기준을 발전시킨다.

### 완성 판정 (= §13 루프 닫힘)
```text
작업 시작
→ preflight 기억 호출 (Stage 3)
→ LLM 판단 보정
→ 위험 시 반문 (Stage 3)
→ 작업 수행
→ 새 why 후보 생성 (Stage 1 입력 + 대화 capture)
→ 사람 SAVE / approve
→ 반복 패턴은 하네스 승격 (Stage 4)
→ 다음 작업에서 다시 preflight로 호출
```
이 루프가 끊김 없이 돌면 빙구팩 완성. **지금은 이 루프의 "저장·공유" 절반만 있고, "자동 채움·꺼내 쓰기·반문·자기개선" 절반이 Stage 1~4로 채워진다.**

### 한 줄
㉮ 입력 어댑터는 **Stage 1의 첫 칸**이다. 전체 완성까지는 Stage 1~4(P0~P3)를 차례로 닫아야 하며, 매 단계의 산출물이 다음 단계의 연료가 된다.
