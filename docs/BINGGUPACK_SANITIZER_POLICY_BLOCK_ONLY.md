> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# BingguPack 트랙1 — sanitizer 정책: 차단만 유지 + 수동 해제(whitelist 예외) (POLICY)

> **상태라인(표준):** `marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)`
>
> **상태: 정책 결정 문서(2026-06-08). docs only · 코드 구현 0 · enum 확정 0 · production write 0.**
> 0번 액션 B 채택 = "차단만 유지 + 차단 내역 확인 후 수동 해제". 자동 sanitizer/치환은 채택하지 않음(HOLD 유지).
> production/OpenCrab/store/DB/server/bid-engine write·apply/ingest/merge/push·v09/ARMED·실자료 외부전송·raw PII/secret 출력·team_paid 코드·GitHub repo 생성/push 0.
> 검증 표현: "production 보장"·"보안 완성" 금지 → "현재 fixture/temp 기준 추가 노출 미검출"만.
>
> - 작성일: 2026-06-08
> - scope: project:openbinggu / 트랙1 개인용 GitHub 공개 sanitizer 정책
> - 상위: `BINGGUPACK_TRACK1_FAILCLOSED_PUBLISH_GUARD_DESIGN.md`(internal design doc — not included in public repo) · [PUBLIC_RELEASE_CHECKLIST](BINGGUPACK_PUBLIC_RELEASE_CHECKLIST.md) · [PERSONAL_TRACK_BASELINE](BINGGUPACK_PERSONAL_TRACK_BASELINE_AND_GITHUB_PUBLISH.md)

---

## 0. 한 줄

dirty/unknown source pointer는 **기본 공개 BLOCK**이며, **자동 sanitizer/치환으로 공개를 허용하지 않는다.** 차단 내역은 **raw 값 없이 reason_code/count/source_pointer_id로만** 확인하고, 사용자가 직접 확인 후 "공개해도 된다"고 승인한 항목만 **수동 whitelist 예외 후보**가 된다. whitelist는 **기본 허용이 아니라 예외 허용**이며 **범위·만료·승인자 기록**을 동반한다. **실제 whitelist 구현은 별도 GO 전 HOLD.**

---

## 1. 정책 결정 (선택지 B 채택)

| 후보 | 채택 여부 | 사유 |
|---|---|---|
| **차단만 유지 + 수동 해제(whitelist 예외)** | ✅ **채택** | fail-closed 유지. 자동 치환 사고 위험 회피. 공개는 사람이 항목별로 확인·승인 |
| 자동 sanitizer/치환 후 공개 허용 | ❌ 미채택(HOLD) | 마스킹 누락/오치환이 곧 영구 공개 유출. 현 단계 구현 안 함 |

> 이 결정으로 상태라인의 **자동 sanitizer는 계속 HOLD**다(정책상 "안 만든다"가 확정). 단 "차단만"이 곧 "절대 공개 불가"는 아니며, **사람이 직접 확인·승인하는 수동 whitelist 예외 경로**를 둔다.

---

## 2. 차단 원칙 (fail-closed, 기본)

- **dirty source pointer**(Windows 절대경로/`file://`·UNC·비공개 unix path·localhost·내부IP·내부도메인) → 기본 공개 **BLOCK**.
- **unknown source pointer**(빈값·판단불가 토큰·미지원 형태) → 기본 공개 **BLOCK** (fail-open 금지).
- **자동 치환/마스킹으로 통과시키지 않는다.** 엔진이 "알아서 가려서 공개"하는 경로는 두지 않음.
- 판정·차단은 기존 `watcher_pack_builder_m0` source pointer 판정(`source_pointer_scan`)을 입력으로 사용(selftest GATE=GO/EXIT=0). 본 문서는 그 위의 **해제(예외) 정책**만 정의.

---

## 3. 차단 내역 확인 형식 (raw 미출력)

차단된 항목을 사람이 검토할 때 노출되는 정보는 **다음 3종으로 한정**한다. raw 경로/URL/원문 값은 출력하지 않는다.

| 필드 | 의미 | 예시(형식만) |
|---|---|---|
| `reason_code` | 차단 사유 코드 | `RESIDUAL_DIRTY` / `MASK_UNKNOWN` |
| `count` | 사유별 차단 건수 | `{dirty: 3, unknown: 1}` |
| `source_pointer_id` | 항목 식별자(원본 값 아님, 안정적 ID/해시) | `sp_0a1b2c3d` |

- raw 값(실제 경로/URL/토큰)은 **확인 단계에서도 출력 0**. 식별자로만 어떤 항목인지 지목.
- 사용자는 이 목록을 보고 "어떤 항목을 직접 확인할지" 결정. 실제 원본 확인은 사용자가 자신의 로컬에서 별도로 수행(시스템이 raw를 뱉지 않음).

---

## 4. 수동 해제 = whitelist 예외 (기본 허용 아님)

### 4-1. 예외 허용 원칙
- whitelist는 **기본 허용 목록이 아니라 예외 허용 목록**이다. 등재되지 않은 모든 dirty/unknown 항목은 **계속 BLOCK**.
- 자동 등재 0. 사용자가 **직접 확인 후 "이 항목은 공개해도 된다"고 명시 승인한 항목만** whitelist 후보가 된다.
- whitelist 통과는 **게이트1(마스킹/판정)의 예외 처리**일 뿐, **게이트2(공개 직전 owner 수동승인)는 별도로 항상 필요**하다. whitelist가 push 자동화를 의미하지 않는다.

### 4-2. whitelist 항목 필수 기록 필드
| 필드 | 의미 | 필수 |
|---|---|---|
| `source_pointer_id` | 해제 대상 항목 식별자 | ✅ |
| `reason_code` | 원래 차단 사유 | ✅ |
| **`scope`(범위)** | 적용 범위(특정 pack/항목 한정, 전역 금지) | ✅ |
| **`expiry`(만료)** | 만료 시점(무기한 금지, 만료 후 재BLOCK) | ✅ |
| **`approver`(승인자)** | 직접 확인·승인한 사용자 | ✅ |
| `approved_at` | 승인 시각 | ✅ |
| `note` | 사용자가 "공개해도 되는 이유" 메모 | 권장 |

- **범위 제한**: whitelist는 항목/pack 단위. "전역 dirty 허용" 같은 광역 예외 금지.
- **만료 필수**: 만료 시각 경과 시 자동으로 다시 BLOCK(예외 영속화 금지).
- **승인자 기록**: 누가 언제 무엇을 예외 처리했는지 추적 가능.
- **재사용 금지**: 1 항목 1 승인. 다른 pack/다른 공개에 자동 승계 금지.

### 4-3. whitelist 적용 후에도 불변
- raw 값은 whitelist 기록에도 저장하지 않음(`source_pointer_id`로만).
- whitelist에 있어도 게이트2 owner 수동승인 통과 전에는 push 0.
- 회귀방지 R1~R3(marketplace_off / enum HOLD / team_billing 없음)은 whitelist와 무관하게 항상 검사.

---

## 4-5. S2 — 공개 pack source pointer 미포함 디폴트 (2026-06-08, 4CLI S2 흡수)

> 4CLI C 반박: source_pointer_id 별칭화는 함정(결정적=빈도분석 역추적 / 랜덤=비가역 디버깅불가 / salt=또 secret). → 별칭을 정교화하는 대신 **공개 pack은 source pointer를 아예 안 넣는 것이 디폴트**.

- **디폴트 = 공개 pack에 source pointer 미포함.** alias/hash/salt 처리한 형태조차 **기본 미포함**(역추적·salt 보관 문제 원천 차단).
- 공개 pack은 claim/요약 + evidence_basis ID 집합(synthetic)만으로 성립. 원본 위치(source pointer)는 공개물에 불필요.
- **필요 시 예외**: 특정 pack에 source pointer가 꼭 있어야 하면 **user/owner 명시 승인 후 별도 정책**으로만. 그때 alias/salt 설계는 별도 결정(현재 HOLD).
- **dirty/unknown은 계속 BLOCK**: 미포함이 디폴트여도, 어떤 경로로든 source pointer가 들어온 pack은 §2 판정(dirty/unknown→공개 BLOCK)을 그대로 받는다. "미포함 디폴트"와 "들어오면 차단"은 둘 다 유지.
- 표시 규율: 차단/점검 결과는 reason_code/count/source_pointer_id(원본 값 아님)만. raw 경로 미출력.

## 5. HOLD / 다음 GO 필요 지점

- **HOLD(구현 금지, 별도 GO 전)**:
  - 자동 sanitizer/치환 로직(정책상 "안 만든다" 확정 — 채택 안 함).
  - **실제 whitelist 저장소/스키마/CLI 구현** — 본 문서는 정책·필드 정의까지만.
  - enum 확정 · production/OpenCrab/store write · apply/ingest/merge/push/v09/ARMED · team_paid 코드 · GitHub repo 생성/push.
- **다음 GO 후보(전부 별도 GO)**:
  1. whitelist 데이터 구조(필드 §4-2) + dryrun check(`whitelist_exception_recorded` 등) 설계.
  2. 차단 내역 확인 출력 형식(§3) selftest fixture 추가 + GATE=GO 재확인.
  3. 실제 GitHub repo 생성·push = 위 통과 + owner 명시 승인 후 별도 결정.

---

## 6. 안전 확인

신규 정책 docs 1개 write + 기존 docs 링크 반영만. 코드 구현·whitelist 구현·selftest 변경 0. production·OpenCrab/store/DB/server·apply/ingest/merge/push·v09/ARMED·bid-engine·실자료 외부전송·raw PII/secret 출력·enum 확정·자동 sanitizer 구현·team_paid 코드·repo 생성/push 0. operating store mtime 불변.
