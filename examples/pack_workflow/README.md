# Pack / Workflow Examples (Lane D)

> **synthetic / example data only.** 실제 개인정보·사업자정보·운영 데이터 0. OpenCrab ingest 0 · production write 0.
> 모든 예제는 README의 lifecycle을 따릅니다:
>
> `goal → workflow design → required packs → required data → evidence capture → candidate nodes/edges → pack validation → publish guard dry-run → consumer smoke → OpenCrab-ready handoff`

BingguPack은 OpenCrab(execution/workflow engine)을 **대체하지 않습니다**. 아래 예제는 BingguPack이 담당하는 **준비·검증·핸드오프** 단계가 어떻게 생겼는지 synthetic data로 보여줍니다. 실제 실행은 OpenCrab의 몫이며, 모든 데이터는 candidate-first / evidence-backed입니다.

## 예제 목록

| # | 예제 | goal | 산출 |
|---|---|---|---|
| 1 | `travel/` | 여행 일정 추천 워크플로우용 pack 준비 | [sample_input.json](travel/sample_input.json) — packs/data + candidate nodes/edges |
| 2 | `patent_intel/` | 특허 인텔리전스 pack 준비 | [sample_input.json](patent_intel/sample_input.json) — evidence→candidate→handoff |
| 3 | `restaurant_brand/` | 레스토랑/브랜드 컨셉 workflow pack | [sample_input.json](restaurant_brand/sample_input.json) — concept/menu/positioning |
| 4 | `generic_handoff/` | generic OpenCrab-ready handoff | [sample_input.json](generic_handoff/sample_input.json) — validation + handoff manifest |

> 4개 예제 전부 synthetic sample을 포함합니다. 모든 sample은 `ingest_performed=false` / `production_write=false`이며, BingguPack이 멈추는 경계(검증된 candidate-first manifest)까지만 보여줍니다. 실제 ingest/실행은 OpenCrab의 몫이며 HOLD입니다.

## travel/ 예제 흐름 (synthetic)

1. **goal** — "사용자에게 2박3일 여행 일정을 추천한다."
2. **workflow design** — 추천 워크플로우 = 숙소 pack + 관광지 pack + 이동 pack.
3. **required packs** — `lodging_pack`, `attraction_pack`, `transport_pack`.
4. **required data** — 각 pack이 필요로 하는 필드(예: 숙소명·지역·가격대 / 관광지명·카테고리 / 노선·소요시간). **전부 synthetic.**
5. **evidence capture** — 각 데이터 항목에 출처 evidence(예: `synthetic_source#1`)를 1:1로 붙임(provenance ≠ evidence 원칙).
6. **candidate nodes/edges** — 5종 노드(개념/상태/판단/문서/증거)로 변환, edge마다 evidence id.
7. **pack validation** — `pack_build` → `pack_validate` (구조·evidence 정합 fail-closed).
8. **publish guard dry-run** — `publish_guard_dryrun` (외부 노출 전 게이트).
9. **consumer smoke** — `consumer_smoke` (downstream이 안전하게 읽는지).
10. **OpenCrab-ready handoff** — handoff manifest로 OpenCrab이 받을 수 있는 형태까지만. **실제 ingest는 안 함(owner 승인 HOLD).**

샘플 입력: [`travel/sample_input.json`](travel/sample_input.json) — 전부 synthetic.

## 안전 원칙

- synthetic data만. 실제 PII/사업자/운영 데이터 절대 금지.
- candidate-first: 어떤 예제도 자동 저장/확정/ingest하지 않음.
- publish guard / consumer smoke는 **dry-run/read**로만.
- OpenCrab Cloud ingest · production write · marketplace = HOLD.
