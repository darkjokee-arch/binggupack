# BingguPack — Personal Speaker Axis (화자 축) 설계

> v1.12.0 신기능. 사용자 발화(owner)와 AI 요약(ai)을 **따로 저장**하고 **수용/반박/수정 엣지**로 연결하며, **양방향 신뢰도**(내 직감·AI 반박 적중률)를 기록한다. 빙구팩이 "AI 작업일지"가 아니라 **"사용자 본체"** 를 쌓게 하는 핵심 축.

## 1. 왜 필요한가 (동기)

빙구팩의 목표는 사용자 개인 온톨로지 / 최종적으로 **사용자 AGI화** 다. 그런데 화자 축 도입 전에는 구조적 갭이 있었다:

- 저장된 노드 대부분이 **AI가 정리한 작업 회고**(교훈·버그패턴)였고, **사용자 본인의 발화·선호·판단 원문**은 거의 들어가지 않았다.
- `semantic_subtype`에 `선호`가 있는데 실제 적재는 0건 — 사용자 취향·가치관이 노드화되지 않았다.
- 누가 한 말인지(화자) 구분하는 칸이 없어, 사용자 발화와 AI 발화가 뭉뚱그려졌다.

즉 사용자를 학습하라고 만든 장부에 **사용자가 안 들어가고 있었다.** 화자 축은 이 갭을 메운다.

## 2. 무엇을 담는가

| 화자 | 담는 것 |
|---|---|
| **owner** (사용자) | 원인 진단, 지적, **사용자 직감** |
| **ai** (AI) | 수정, 직감 **수용**, 직감 **반박** |

두 노드를 **각각 독립 저장**하되, 둘 사이를 **동사형 엣지**로 연결한다:

```
[owner 직감 노드]  ←(evidence_supports)─ [owner 자기증거]
       ▲
       │ (ai_refutes)            ← 페어 엣지 (수용/반박/수정)
       │
[ai 요약 노드]     ←(evidence_supports)─ [ai 자기증거]
```

- **owner만/ai만/연결** 3중 활용: 사용자 발화만 모아보기, AI 회고만 모아보기, 맥락 타고 "이 상황에서 사용자는 이렇게" 보기.
- 기존 5종 노드 + conv-self evidence + evidence_supports 골격은 **그대로(additive)** — 화자 칸과 페어 엣지만 위에 얹힌다.

## 3. 데이터 모델 (비파괴 확장)

- `nodes.speaker` (`owner`/`ai`/`NULL`) — 기존 노드는 NULL(소급 안 건드림). `semantic_subtype`·`use_count`와 동일한 비파괴 `ALTER ADD COLUMN` 패턴.
- 페어 엣지 relation: `ai_accepts`(수용) / `ai_refutes`(반박) / `ai_revises`(수정). 전 엣지 evidence 증빙 의무 충족(ai 자기증거 참조).
- `hit_events` 테이블 — 양방향 신뢰도 이벤트 로그(append-only). 조회 시 산정.

> ⚠️ **store_checksum 함정 주의**: `store_checksum`은 `speaker` 컬럼을 **제외**(명시 컬럼 projection)한다. 포함하면 `ALTER` 후 기존 운영 ledger의 audit anchor(after_hash)와 어긋나 `verify_tail_state`가 정상 노드를 변조로 오판한다. `verify_chain`(audit_log 기반)만으론 못 잡고 `verify_tail_state`(checksum anchor 기반)로 잡힌다.

## 4. 페어 저장 (`save_paired`)

한 번의 저장이 만드는 것:
- owner 노드(speaker=owner) + ai 노드(speaker=ai) 각각 독립 + 각 자기증거 + 페어 엣지
- **owner 단독 허용**: `ai_text`가 없으면 owner 노드 1개만(순수 직감 — 억지 ai 노드/엣지 생성 금지).
- **원자성**: 단일 pack → `staging_apply` 1회(부분커밋 시 전체 롤백).
- **dangling 방지**: 페어 중 한쪽이라도 기존재면 전체 skip(`pair_partial_exists`).

## 5. 양방향 신뢰도 (`binggu_hit_stats.py`)

사용자도 AI도 틀릴 수 있다. 그래서 신뢰도는 **참고 가중치이지 맹종 스위치가 아니다.**

- **별도 분모**: owner 직감 적중률 / ai 반박 적중률을 따로 산정(같은 표본 이중계상 금지).
- **시간 감쇠**: 반감기 30일 — 낡은 적중률이 새 판단을 오염시키지 않게.
- **표본 게이트**: N<5면 신뢰도 미산정(과소표본 편향 차단).
- **균형 표시**(`both_sides`): 한쪽 편들지 않고 양쪽 적중률을 함께 보여준다. 최종 판단은 사람+근거.

`owner` 직감을 `resolve`(성공/실패)하면, 페어 relation으로 ai 입장이 자동 도출된다(수용=같은편, 반박=반대, 수정=중립).

## 6. CLI

```bash
# 페어 저장 — 내 직감 + AI 요약, 수용/반박/수정으로 연결
binggu pair "<내 직감>" "<AI 요약>" --relation refutes --confirm "PAIR ai_refutes owner:1 ai:1"
# 순수 직감 단독 (AI 노드 안 만듦)
binggu pair "<내 직감만>" --confirm "PAIR owner:1"
# 양방향 신뢰도 보기 (read-only)
binggu trust
# 결과 기록 → 적중률 누적
binggu resolve <n> <id8> --outcome 성공
# 저장 의도 라우팅 (신규/수정/결과 안내, read-only)
binggu route "<발화>"
```

## 7. 안전 모델 (헌법 정합)

화자 축은 빙구팩 헌법을 그대로 지킨다 — 최종 검증 워크플로우에서 **5항 위반 0** 확인:

| 헌법 | 화자 축 경로 |
|---|---|
| candidate-only (promotion_allowed=0) | `staging_apply` 고정값, speaker는 보조 컬럼 |
| `G4_no_auto` (actor=human) | `save_paired`·`record_resolution` 둘 다 게이트 |
| PII/secret 제외 | `_pick_one_node`에서 차단, hit_events는 sentence 미저장 |
| 사람 confirm 게이트 | `PAIR <relation> owner:N ai:M` 정확 일치 |
| 전 엣지 evidence | 자기증거 + 페어 엣지 evidence_refs |

## 8. 검증

- `binggu pair/trust/route` 실명령 e2e PASS.
- selftest 전수 GO: binggu 40/40 · candidate_save 19/19 · staging 16/16 · deprecate 23/23 · replace 19/19 · promote 17/17.
- 운영 ledger 마이그레이션 무손상: speaker/hit_events 추가 후 291노드 보존 · `verify_tail_state`/`verify_chain` True · 백업 동반.

## 9. 남은 것 (선택)

- publish 게이트: `graph_preview`/`graph_confirm`/`cloud_pack_export`의 SUPPORTS 단일 화이트리스트를 `ALLOWED_RELATIONS`로 일반화(cloud export 단계만 필요, 로컬 저장은 무영향).
- 기존 노드 speaker 소급 backfill(NULL로 둬도 정상 동작).
