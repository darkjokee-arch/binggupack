# BingguPack × Paperthin 선택적 통합 검토

> 검토 기준: BingguPack `main` [`9e5a66b`](https://github.com/darkjokee-arch/binggupack/tree/9e5a66b8117bce35b7ac7c2ed430fa5b5bc22578), Paperthin `main` [`3bca079`](https://github.com/LilMGenius/paperthin/tree/3bca079a51bcfff5dafb53d1d7f9f523d66ee317)  
> 범위: `catchup`, `readchk`, `hate`, `sip`, `nba`, `factchk`, `mandela` 7개 패턴만. 코드·설정·hook·운영 장부 변경 없음.

## 결론

Paperthin 런타임이나 스킬 묶음은 도입하지 않는다. 7개 패턴은 BingguPack의 기존 CLI/MCP와 코어 함수를 호출하는 얇은 판단 어댑터로만 재해석한다.

현재 `main`에는 이미 다음 폐루프의 핵심 데이터 경로가 있다.

```text
recall/preflight
  → recall_trace
  → 사용 판정
  → recall_run_outcomes(application + result + evidence digest)
  → 집계
```

따라서 새 기억 엔진, 새 승인 체계, 새 정본을 만들 필요가 없다. 가장 큰 실제 공백은 **repo live state + canonical memory + 이전 outcome을 한 번에 복원하는 재진입 표면**이다. 1차 구현은 읽기 전용 `binggu catchup` 하나로 제한한다.

## 1. 현재 기능 대응표

| Paperthin pattern | 현재 BingguPack 대응 기능 | Gap 분류 | 핵심 Gap | 도입 필요성 |
|---|---|---|---|---|
| `catchup` | [`preflight_context()`](../binggupack/pack/recall.py#L613), [`hot_recall()`](../binggupack/pack/fresh_index.py#L658), [`_u_status()`](../binggupack/mcp/server_handlers.py#L684), [`list_run_outcomes()`](../binggupack/pack/outcome_attribution.py#L127) | 부분적으로 있음 | repo branch/commit/diff/test/plan과 memory/outcome을 합친 단일 briefing 없음 | **매우 높음** |
| `readchk` | [`classify_situation()`](../binggupack/pack/recall_trace.py#L93), [`preflight_context()`](../binggupack/pack/recall.py#L613), [`build_answer_rules()`](../binggupack/pack/answer_rules.py#L192) | 부분적으로 있음 | 사용자 의도 재구성, 살아남은 ambiguity, recall query 생성 계약 없음 | 높음 |
| `hate` | [`detect_conflicts()`](../binggupack/safety/contrast_protocol.py#L153), preflight의 `avoid_patterns`/`risk_level` | 부분적으로 있음 | 가장 치명적인 반론 1개와 가장 싼 falsification test로 축약하지 않음 | 중간 |
| `sip` | [`classify()`](../binggupack/classifier/capture_classifier.py#L101), capture preview, [`build_close_summary()`](../binggupack/review/session_close.py#L622), [`prepare_selected()`](../scripts/openbinggu_conversation_candidate_save.py#L124) | 부분적으로 있음 | 완료 검증 결과에서 재사용 가능한 typed SAVE candidate를 제안하는 명시적 post-work 계약 없음 | 높음 |
| `nba` | preflight, [`build_answer_rules()`](../binggupack/pack/answer_rules.py#L192), [`aggregate_run_outcomes()`](../binggupack/pack/outcome_attribution.py#L190) | 부분적으로 있음 | 목표·blocker·과거 outcome을 근거로 단일 행동을 고르고, 어떤 recall이 선택을 바꿨는지 남기는 계약 없음 | 높음, `catchup` 후 |
| `factchk` | evidence graph, [`evidence_locator_coverage()`](../binggupack/evidence/locator.py#L72), provenance/locator 저장 | 부분적으로 있음 | 외부 사실 주장 탐지·외부 출처 대조·판정 결과를 candidate evidence에 연결하는 단계 없음 | 조건부 높음 |
| `mandela` | Hybrid-AGI blind/commit-reveal, 결정적 selftest, recall/outcome 집계 | 부분적으로 있음 | recall 사용군/비사용군 비교, scorer-designer 분리, expected-answer leakage 점검을 묶은 behavioral eval 계약 없음 | eval 한정 높음 |

### 중요한 현재 상태

- 회상: CLI [`cmd_recall()`](../binggu.py#L397), MCP [`_u_recall()`](../binggupack/mcp/server_handlers.py#L394)·[`_u_preflight()`](../binggupack/mcp/server_handlers.py#L470).
- 회상 trace: [`record_trace()`](../binggupack/pack/recall_trace.py#L281), 효용 판정: [`record_outcome()`](../binggupack/pack/recall_trace.py#L331).
- 결과 귀속: [`record_run_outcome()`](../binggupack/pack/outcome_attribution.py#L68). `applied_node_ids`는 반드시 해당 trace가 회상한 노드의 부분집합이고, evidence digest 없이는 fail-closed다.
- 저장 후보: capture/classifier가 후보만 만들고, [`save_selected()`](../scripts/openbinggu_conversation_candidate_save.py#L232)·[`save_paired()`](../scripts/openbinggu_conversation_candidate_save.py#L399)가 사람 save-n 앵커를 재검증한다.
- hosted bundle: [`commit_bundle()`](../scripts/binggu_hosted_bundle.py#L179)은 hosted intent 묶음 전용이다. 로컬 SAVE 전체의 공통 승인 함수로 오해하면 안 된다.
- exact binding: [`binding_fields()`](../binggupack/safety/trusted_approval.py#L160)과 save gate의 `preview_ref + indices`가 payload를 고정한다.

## 2. 추천 architecture

### 2.1 경계

```text
Paperthin-derived adapter
  입력: 사용자 요청, repo snapshot, BingguPack read 결과, 검증 증거
  출력: briefing / objection / action / SAVE candidate proposal
  권한: 판단·정리·제안만

BingguPack canonical core
  recall → dedupe/conflict/evidence → preview → 사람 SAVE n → commit → outcome
  권한: 기존 게이트를 통과한 영구 상태 변경만
```

공통 금지 규칙:

1. 어댑터에서 `save_selected()`, `save_paired()`, `commit_bundle()` 직접 호출 금지.
2. `ledger.sqlite` 직접 INSERT/UPDATE 금지.
3. candidate가 `actor=human`을 만들거나 전달하는 인터페이스 금지.
4. Paperthin의 글로벌 hook, 설치기, updater, invocation registry를 추가하지 않음.
5. 새 분류 정본을 만들지 않음. proposal의 `decision|lesson|preference|state|procedure|constraint`는 저장 전 표시용이며, 승인 후 기존 `label_kind`/`semantic_subtype` 정본에 매핑한다.

### 2.2 패턴별 연결점

| Pattern | 어디에 | 무엇을 | 인터페이스 | 이유 |
|---|---|---|---|---|
| `catchup` | 신규 `binggupack/pack/catchup.py` + `binggupack/cli/catchup.py`; 첫 단계는 CLI만 | repo snapshot과 기존 recall/outcome을 결정적으로 합성 | `build_catchup(repo_state, preflight, recall, outcomes, limits) -> CatchupBrief` | I/O 수집과 순수 합성을 분리하고 기존 코어를 재사용 |
| `readchk` | agent-side thin adapter; 후속 검증 뒤 MCP 보조 가능 | intent paraphrase, surviving fork, recall query | `{understood_as, surviving_fork?, recall_queries[], source_refs[]}` | 의미 판단은 모델 역할이며 DB 코어에 넣을 이유가 없음 |
| `hate` | 명시 호출형 agent adapter | load-bearing objection 1개 + first nail 1개 | `{root, first_nail, evidence_refs[]}` | 기존 conflict 목록을 다시 만들지 않고 한 개로 합성 |
| `sip` | 작업 종료 agent adapter → 기존 `capture_preview` | 검증 결과에서 SAVE candidate proposal 생성 | `{kind, text, source_refs, external_claims[], confidence}` | proposer와 save authority를 물리적으로 분리 |
| `nba` | `catchup` 구조화 출력 소비자 | 다음 행동 1개와 완료 조건 | `{action, why_now, done_when, trace_ids[], influence[], confidence}` | recall/outcome이 실제 선택에 미친 영향 추적에 필요 |
| `factchk` | `sip`와 save gate 사이가 아니라 **candidate proposal 내부 조건부 단계** | 외부 주장만 외부 출처로 검증 | `{claim, verdict, source_uri, source_digest, checked_at}` | preference·내부 결정·repo state의 불필요한 외부 검증 방지 |
| `mandela` | `tests/behavioral/` 또는 별도 eval 스크립트 | behavioral eval 설계 감사 | 고정 eval manifest + leakage findings | 운영 MCP/CLI 기본 흐름에서 완전히 분리 |

### 2.3 권장 흐름

```text
요청
  → readchk(후속)
  → 기존 preflight/recall
  → 작업
  → 필요 시 hate(명시/고위험만)
  → 검증
  → sip가 proposal만 생성
  → 외부 사실이 있을 때만 factchk
  → 기존 capture preview / dedupe / conflict / evidence
  → 기존 save-n exact binding
  → 기존 SAVE/commit
  → recall_run_outcomes
  → nba(후속)
```

새 세션의 1차 구현 범위:

```text
binggu catchup --repo <path> --query <work>
  → git live state(read-only)
  → preflight_context + why_search/hot_recall
  → list/aggregate_run_outcomes
  → CatchupBrief
  → repo/ledger write 0
```

`CatchupBrief` 권장 형식:

```text
CURRENT STATE
WHAT CHANGED
RELEVANT MEMORY
DECISIONS
KNOWN FAILURES
KNOWN CONSTRAINTS
UNRESOLVED
NEXT BEST ACTION
SOURCES
```

`NEXT BEST ACTION`은 1차에서 새 NBA 추론을 구현하지 않는다. repo의 명시적 blocker/미실행 필수 테스트처럼 객관적인 gate가 하나일 때만 그대로 표시하고, 선택지가 갈리면 `UNRESOLVED`로 남긴다.

### 2.4 진실 우선순위

| 주장 종류 | 정본 | 충돌 시 처리 |
|---|---|---|
| 현재 branch/HEAD/diff/test 상태 | live repo/artifact | live state 우선 |
| 과거 결정·교훈·선호·절차 | active canonical memory + provenance | superseded 항목은 현재 결정과 함께 이력으로만 표시 |
| 성공/실패·적용 여부 | evidence-gated `recall_run_outcomes` | evidence 없는 서술은 `unverified` |
| 채팅 기억 | 비정본 힌트 | 단독 근거로 사용 금지 |

충돌을 자동 해결하거나 memory를 덮어쓰지 않는다. `CURRENT STATE`와 `RELEVANT MEMORY`가 다르면 `UNRESOLVED`에 두 근거를 함께 보여 준다.

## 3. 중복 구현하지 않을 기능

1. **recall/scoring**: `why_search()`, `hot_recall()`, `preflight_context()` 재사용. 새 Paperthin scorer 금지.
2. **use/trace/outcome**: `recall_trace`와 `outcome_attribution` 재사용. 호출 횟수용 별도 telemetry 금지.
3. **candidate extraction**: `capture_classifier`, capture preview, `prepare_selected()` 재사용. `sip` 전용 저장 포맷 금지.
4. **dedupe/conflict**: 기존 match policy·contrast protocol 재사용.
5. **evidence/provenance**: 기존 evidence graph와 locator를 사용. factchk 전용 정본 DB 금지.
6. **approval/commit/rollback**: save-n exact binding, trusted approval, staging snapshot, hosted `commit_bundle()` 유지. 새 승인 UI/토큰 금지.
7. **SSOT**: `ssotize` 스킬을 만들지 않고 기존 canonical memory를 참조한다.
8. **blind 검증 기반**: Hybrid-AGI commit-reveal 자산은 mandela eval에서 재사용 가능하지만, 운영 기억 승격과 behavioral score를 같은 것으로 취급하지 않는다.

## 4. 위험 요소와 방어책

| 위험 | 실패 형태 | 방어책 | 회귀 증명 |
|---|---|---|---|
| approval bypass | sip/factchk가 candidate를 곧바로 저장 | adapter 패키지에서 save/commit 함수 import 금지; candidate output only | monkeypatch로 저장 함수 호출 시 즉시 실패 + ledger mtime/row count 불변 |
| auto-save | 작업 종료 시 자동 영구화 | 자동 hook 0, 명시 preview만; `SAVE n` 재도출·exact binding 유지 | AI actor·confirm 위조·stale preview 전부 G4_no_auto/BLOCK |
| duplicated truth | repo state를 memory에 복제 저장 | catchup 결과는 휘발성 view; 현재 상태는 live repo를 참조 | 두 번째 실행에서 repo 변경이 즉시 반영되고 memory 신규 행 0 |
| provenance loss | 요약이 source/evidence를 떼어냄 | 모든 섹션 항목에 source ref; superseded chain 보존 | source 없는 claim은 `unverified`, 삭제/UPDATE 0 |
| eval self-confirmation | 설계자/모델/채점자가 같은 답을 강화 | baseline/Binggu/Binggu+pattern, 고정 manifest, blind scorer, 외부 ground truth | scorer에 treatment label 미노출; manifest hash 고정 |
| excessive context | diff·memory·outcome 전문을 한 번에 주입 | 항목/바이트 예산, diff stat 우선, top-K recall, drill-down | hard cap 초과 시 잘림 사유와 omitted count 표시 |
| skill invocation 충돌 | Paperthin `catchup`과 Binggu adapter가 동시에 자동 발동 | 제품 표면은 `binggu catchup`; 글로벌 skill/hook 설치 0 | 기존 skill directory/config 변경 0 |
| stale-state 오판 | memory의 완료 주장과 dirty repo가 충돌 | live repo 우선, 자동 화해 금지, `UNRESOLVED` 노출 | 상반 fixture에서 둘 다 source와 함께 표시 |
| telemetry=memory 혼동 | trace append를 자동 SAVE로 오해 | ledger와 trace store를 분리 표기; trace는 기존 opt-in일 때만 | trace OFF write 0, ON이어도 ledger 불변·원문 미저장 |
| 라이선스/출처 누락 | Paperthin 문구를 사실상 복제 | 패턴은 재작성하고 Paperthin MIT 출처를 문서에 명시; verbatim 복사 시 NOTICE 검토 | 배포 전 tree/license scan |

## 5. 최종 판정

| Pattern | 판정 | 이유 | 순서 |
|---|---|---|---:|
| `catchup` | **GO** | 가장 큰 미싱링크이며 read-only로 검증 가능. 기존 recall/outcome 폐루프를 실제 작업 재진입에 사용하게 함 | 1 |
| `sip` | **MODIFY** | 가치 높지만 Paperthin 원본은 품질 오케스트레이터다. BingguPack에서는 SAVE candidate proposer로 제한해야 함 | 2 |
| `nba` | **MODIFY** | 단일 행동 선택은 유효하나 catchup과 influence trace가 먼저 필요 | 3 |
| `readchk` | **MODIFY** | recall query 전처리로 유효. 원 요청 대체·과도한 질문을 금지하는 계약 필요 | 4 |
| `hate` | **MODIFY** | 상시 자동 호출 금지. 기존 conflict를 한 반론/한 실험으로 합성하는 명시형만 | 5 |
| `factchk` | **MODIFY** | 외부 사실 주장에만 조건부. evidence/provenance는 기존 정본에 연결 | 6 |
| `mandela` | **MODIFY** | recall/behavioral eval 전용. 운영 기본 흐름과 분리 | 7 |

이번 범위 밖 `re0`, `ssotize`, `prism` 및 나머지 Paperthin 스킬은 **SKIP**이다. 필요성이 보여도 후속 후보로만 기록한다.

## 6. 1차 구현 선택: `binggu catchup`

### 선택 이유

1. BingguPack의 recall/outcome 기능은 이미 구현됐지만 세션 복원에서 한 화면으로 연결되지 않는다.
2. repo와 ledger를 쓰지 않는 어댑터라 승인 경계를 건드리지 않는다.
3. 이후 `nba`, `readchk`, behavioral eval이 공통으로 소비할 구조화된 상태 봉투를 만든다.
4. 실패 기준이 명확하다: 브리핑이 live state와 다르거나, 출처가 없거나, context budget을 넘거나, 저장이 발생하면 실패다.

### 최소 변경안

첫 PR은 다음 한 기능만 포함한다.

- `binggupack/pack/catchup.py`: 입력 dict를 `CatchupBrief`로 합성하는 순수 함수.
- `binggupack/cli/catchup.py`: allowlisted git read 명령과 기존 BingguPack read API 호출.
- `binggu.py`: `catchup` 서브커맨드 디스패치.
- 표적 테스트 1개. MCP 노출, hook, sip/nba/factchk/mandela 구현은 포함하지 않는다.

기본 실행은 완전 read-only다. 기존 `recall_config.trace_enabled`가 켜진 환경에서만, 회상된 node metadata를 기존 `record_trace()`에 `kind=catchup`으로 남기는 opt-in telemetry를 후속 소규모 변경으로 검토한다. 1차 PR에서는 trace write도 생략해 무쓰기 기준선을 먼저 고정하는 편이 안전하다.

### 회귀 테스트

| 테스트 | 합격 기준 |
|---|---|
| clean repo | branch, HEAD, last commit, clean 상태가 실제 git 결과와 일치 |
| dirty repo | 변경 파일명·stat만 표시하고 diff 전문/secret 원문 미노출 |
| detached HEAD / non-git | 예외 없이 상태를 명시하고 memory briefing은 계속 생성 |
| no ledger / no trace store | 빈 memory/outcome을 정상 상태로 처리, 파일 자동 생성 0 |
| active + superseded decision | active가 `DECISIONS`, superseded는 이력 표식과 provenance로만 노출 |
| known failure + constraint | `KNOWN FAILURES`/`KNOWN CONSTRAINTS`에 정확히 분리 |
| unresolved outcome | `pending_traces` 및 최근 미결 항목이 `UNRESOLVED`에 표시 |
| conflicting repo/memory | 자동 해결하지 않고 두 source를 함께 표시 |
| context budget | top-K·문자수 상한 준수, omitted count와 drill-down 힌트 표시 |
| no mutation | 실행 전후 git status 동일, `ledger.sqlite`·`recall_trace.sqlite` size/mtime/hash 동일 |
| approval boundary | `approval_requests`, save gate, candidate, node, edge 행 증가 0 |
| determinism | 동일 snapshot/clock 입력 두 번의 구조화 출력이 byte-identical |

필수 기존 회귀:

```text
python -m pytest <catchup target tests>
python binggu.py --selftest
python scripts/binggu_save_ref_binding_selftest.py
python scripts/binggu_p1b1_bundle_atomicity_selftest.py
python scripts/binggu_outcome_attribution.py --selftest
python scripts/binggu_publish_run_all_selftests.py
python scripts/openbinggu_public_tree_scan.py --tree . --public
```

### Behavioral eval 설계

동일한 고정 시나리오를 다음 세 군에 배정한다.

```text
baseline             = repo만 읽음
BingguPack only       = 기존 preflight/recall
BingguPack + catchup  = repo + recall + outcome 통합 briefing
```

1차 측정값:

- 잘못된 작업 착수율
- blocker 누락률
- superseded decision 오사용률
- recall이 제안 행동을 바꾼 비율
- 같은 실패 반복률
- 완료율
- 입력/출력 토큰, 실행시간
- 인간 정정 횟수

`use_count`와 호출 횟수는 성공 지표가 아니다. treatment label을 가린 별도 scorer가 고정 정답·실제 repo outcome으로 채점하고, 동일 모델이 설계와 채점을 모두 맡는 경우 결과를 참고치로만 취급한다.

## 7. 설계 검토 기록

4-CLI debate 결과는 `catchup` 1차 구현에 수렴했다.

| Participant | Recommendation | Raw Conf | Normalized Conf |
|---|---|---:|---:|
| Gemini | catchup | 0.95 | 1.00 |
| OpenAI | catchup | 0.85 | 0.85 |
| Ollama | readchk 선행 대안 | 0.80 | 0.70 |
| Codex CLI | catchup | 0.91 | 0.91 |

- 다수: 3/4 `catchup`
- 다수 평균 정규화 신뢰도: 0.92
- `ship_recommendation`: **GO**
- 묵시적 가정: repo snapshot과 memory/outcome을 제한된 예산 안에 합성할 수 있어야 한다. context cap 회귀 테스트로 검증한다.
- 단계 통합: 첫 PR은 catchup만 구현하고 NBA 추론은 합치지 않는다. 객관적 blocker가 없으면 선택을 보류한다.

## 8. 출처

- Paperthin 7개 원본: [`catchup`](https://github.com/LilMGenius/paperthin/blob/3bca079a51bcfff5dafb53d1d7f9f523d66ee317/skills/coil/catchup/SKILL.md), [`readchk`](https://github.com/LilMGenius/paperthin/blob/3bca079a51bcfff5dafb53d1d7f9f523d66ee317/skills/depth/readchk/SKILL.md), [`hate`](https://github.com/LilMGenius/paperthin/blob/3bca079a51bcfff5dafb53d1d7f9f523d66ee317/skills/depth/hate/SKILL.md), [`sip`](https://github.com/LilMGenius/paperthin/blob/3bca079a51bcfff5dafb53d1d7f9f523d66ee317/skills/depth/sip/SKILL.md), [`nba`](https://github.com/LilMGenius/paperthin/blob/3bca079a51bcfff5dafb53d1d7f9f523d66ee317/skills/coil/nba/SKILL.md), [`factchk`](https://github.com/LilMGenius/paperthin/blob/3bca079a51bcfff5dafb53d1d7f9f523d66ee317/skills/depth/factchk/SKILL.md), [`mandela`](https://github.com/LilMGenius/paperthin/blob/3bca079a51bcfff5dafb53d1d7f9f523d66ee317/skills/depth/mandela/SKILL.md).
- Paperthin license: MIT, Copyright 2026 LilMGenius. 이번 산출물은 원본 런타임·파일을 vendoring하지 않고 패턴을 BingguPack 경계에 맞게 재설계했다.
