# BingguPack — owner_accepted 재설계 R2 (정본, 2026-06-12)

> v1(`BINGGUPACK_PERSONAL_ACCEPTED_STATE_DESIGN.md`)을 supersede. 적대 검토
> (`BINGGUPACK_PERSONAL_ACCEPTED_STATE_REVIEW.md`) HIGH2+MED4+LOW1 전건 해소 +
> 4cli 13지시(`BINGGUPACK_V1_BOUNDARY_DEBATE_CONCLUSION.md`) 반영. **설계만 — 구현은 별도 GO.**

## 1. 어휘 확정 (D7 해소)

`owner_retained`(유지)가 "내 장부에 유지" 의미엔 더 정직하나, 철회(retract) 동사쌍·기구현 confirm
ACTION 동사 일관성에서 `ACCEPT/UNACCEPT`가 우월 → **`owner_accepted` 확정**.
용어집 1줄 박제: **accepted ≠ 승인(approval)** — 업로드 G7 owner 승인·opencrab approval과 무관한 내부 기록.

## 2. 스키마 — 현재 view 테이블 + audit 이력 (HIGH-D1 해소)

```sql
CREATE TABLE IF NOT EXISTS owner_acceptances(
    node_id TEXT PRIMARY KEY,                                  -- 재수용 = 행 갱신(UPDATE)
    status  TEXT NOT NULL CHECK(status IN('accepted','retracted')),
    reason  TEXT NOT NULL,                                     -- 사유 필수
    ts      TEXT NOT NULL);
```

- **현재 상태 = 이 한 행** (accept→INSERT / unaccept→UPDATE status='retracted' / 재수용→UPDATE status='accepted'). UNIQUE↔보존형 모순 소멸: 철회·재수용 모두 행 갱신이라 충돌 없음, 행은 영구 보존.
- **이력 = audit_log** (`owner_accept` / `owner_unaccept` action append) — 기존 audit chain 그대로.
- **event table 신설 금지 근거** (13지시 결론 2-1): 기존 장부는 전부 state UPDATE 모델(nodes.state, deprecations) — append-only 이벤트 모델 공존 시 이중 상태모델·"최신 이벤트 = 진실" 재구성 코드가 생겨 검증 면적만 늘어남. append-only는 audit_log 단일 위치로 한정.
- v1의 "deprecated 도장과 대칭" 표현 **삭제** — g3에 un-deprecate 경로가 없어(일방향 도장, `already_deprecated` 영구 차단) 대칭 비교 대상이 실재하지 않음. accepted는 가역(철회/재수용), deprecated는 비가역 — 서로 다른 모델임을 명시.

## 3. confirm — 이중 바인딩 + 실행 직전 재검증 (HIGH-D4 해소)

기각 UX 기구현 패턴(`openbinggu_candidate_deprecate_ux.py` deprecate_from_list) **동형 재사용**:
1. confirm 문구 정확 일치 = `ACCEPT <n> <id8>` / 철회 = `UNACCEPT <n> <id8>` (인덱스 단독 금지, 13지시 결론 4).
2. confirm은 **transaction 밖**에서 수신(사람 대기 중 잠금 0) → 실행 직전 **목록 재실행**(호출자 목록 객체 불신) → index 범위 검사 → **id8 재검증**(`node_id8(rows[n-1]) != id8` → BLOCK `node_hash_mismatch`). stale 목록 시프트 TOCTOU 차단.
3. 입구 게이트 동일: actor=auto/reader BLOCK(`G4_no_auto`) · 사유 필수 · BLOCK도 audit append.

## 4. deprecated wins (MED-D2 해소)

- 기각(deprecated) 노드 ACCEPT/UNACCEPT 시도 = **BLOCK `target_not_active`** (목록 재실행 후 state 검사).
- accepted **후** 기각되는 경우: acceptance 행은 보존(자동 철회 0 — 자동 변경 금지선) — 단 **모든 조회·필터·업로드 후보 선별에서 deprecated 우선**(노출 = accepted ∩ active만). query helper와 G0 필터 양쪽에 코드 불변식으로 박음(13지시 결론 1). 목록 뷰엔 충돌 배지(`accepted+deprecated`)만 표시.

## 5. resolve 실패 × accepted 공존 (MED-D3 해소)

둘 다 기록 보존 — resolve는 record-only, acceptance도 record-only라 충돌 아님. **자동 연쇄 0**
(실패→자동 unaccept 금지, 13지시 결론 5). 목록 뷰에 양쪽 동시 표시(`accepted · resolve:실패`) +
리마인드 목록 포함 — 사람 검토 유도까지만, 판단은 owner.

## 6. 업로드 경계 — G0 분리 (MED-D5·D6 해소)

- v1 "E preflight의 G5 필터에 연결" **정정 1줄**: 업로드 후보 선별은 G5(candidate-only 검증 게이트)가 아니라 **별도 G0(후보 선별 입력) 단계** — G0 = `accepted ∩ active` 필터로 후보 목록 생성, G1~G7은 그 결과를 검증. 검증↔선별 혼합 금지.
- "필터 입력일 뿐"을 **음성 selftest로 강제** (문구 약속 아님):
  - (a) accepted 행 존재 + G7 owner 승인 미통과 → 업로드 **BLOCK** (accepted만으로 업로드 불가)
  - (b) accepted 0건이어도 owner가 명시 지정+G1~G7 통과 → 업로드 **가능** (accepted는 필수조건 아님)
  - (c) accepted ∩ deprecated 노드 → G0에서 제외 (deprecated wins)
  - (d) accepted 저장 직후 자동 업로드 연쇄 발생 0 (13지시 결론 5)

## 7. 구현 게이트 — selftest 케이스 (구현 별도 GO)

정상 수용(INSERT+audit) / 철회(UPDATE retracted+audit, 행 보존) / 재수용(UPDATE accepted, UNIQUE 충돌 0) /
기각 노드 ACCEPT BLOCK(`target_not_active`) / stale 목록 id8 mismatch BLOCK / confirm 문구 불일치 BLOCK /
사유 없음 BLOCK / actor=auto BLOCK / index 범위밖 BLOCK / 중복 수용 BLOCK(`already_accepted`) /
§6 음성 4케이스 / audit chain INTACT / confirmed 0·promotion 0 전수 / 운영 store 불변 / temp 정리.
전 단계 temp selftest + real 실연 + clean clone + scan 5단 의무 (real staging 적용 = 별도 GO).
