> OpenBinggu is the legacy/internal codename for BingguPack.

# OpenBinggu 제품 방향 — 2트랙 재정의 (DIRECTION DOC, DESIGN ONLY)

> **상태라인(표준):** `marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)`

- 작성일: 2026-06-08
- 상태: **제품 방향 정의 only.** 결정·구현·production write 0. OpenCrab/store/DB/bid-engine write 0. apply/ingest/merge/push/v09/ARMED 0. enum 확정 0. 실자료 외부전송 0.
- 상위: [BUSINESS_SCOPE_DECISION_BRIEF](OPENBINGGU_BUSINESS_SCOPE_DECISION_BRIEF.md)(4선택지 brief) · [P0_BUNDLE_STATUS_UNDECIDED_HOLD](OPENBINGGU_P0_BUNDLE_STATUS_UNDECIDED_HOLD.md) · [PACK_APPROVAL_THREE_PATH_DESIGN](OPENBINGGU_PACK_APPROVAL_THREE_PATH_DESIGN.md).
- 표현 규칙: "production 보장"·"보안 완성" 금지. 검증 표현은 "현재 fixture/temp 기준 추가 노출 미검출"만.

---

## 0. 한 줄

기존 4선택지(private / shared_with / public / paid marketplace)를 **2트랙 제품 방향**으로 단순화한다: **① 개인용 버전(먼저 완성할 기본) → ② 팀 유료 버전(개인용 위 확장)**. **불특정 다수 pack 거래소(marketplace)는 현재 목표가 아니며 BLOCK으로 분리**한다.

---

## 1. 2트랙 정의

### 트랙 1 — 개인용 버전 (기본 제품, 먼저 완성)
- **사용 형태**: 각 사용자가 자기 로컬/개인 계정에서 **자기 pack만** 사용.
- **공개성**: GitHub 공개 **가능성을 열어둠**(개인이 자기 pack을 오픈소스로 공개하는 선택). 무료 또는 오픈소스 가능.
- **핵심 기준**: `private/local`.
- **위치**: 가장 먼저 완성하는 기본 버전. 트랙 2의 토대.

### 트랙 2 — 팀 유료 버전 (개인용 위 확장)
- **사용 형태**: 회사/팀 단위로 여러 사람이 같이 사용.
- **필요 기능**: 팀원 초대 · 권한(role) · `shared_with` · 접근제어(access control) · 결제/구독(billing).
- **핵심 기준**: `shared_with + access control + billing`.
- **위치**: 개인용 버전 위에 확장되는 유료 버전.

---

## 2. 4선택지 → 2트랙 매핑 (기존 B brief와의 차이)

| 기존 B brief 4경로 | 2트랙 재정의 | 차이 |
|---|---|---|
| A. private/local | **트랙 1 핵심** | 그대로 = 개인용 기본 |
| (신규) 개인 pack GitHub 오픈소스 공개 | **트랙 1 옵션** | B brief의 "public(외부 LLM 송신)"과 **다른 개념** — 개인이 자기 pack을 공개 저장소에 올리는 것. redaction 체크리스트 필요 |
| B. shared_with | **트랙 2 핵심 구성** | 1급 제품 트랙으로 승격(기존엔 개인 프라이빗의 shared_with 옵션) |
| (신규) team invite/role/billing | **트랙 2 핵심 구성** | B brief엔 access control만 있었음 → billing/구독 추가 |
| C. public(무료 공개, 외부 LLM/생태계) | **DEFER**(트랙 외) | 2트랙 목표 아님. 트랙1 GitHub 공개와 혼동 금지 |
| D. paid marketplace | **BLOCK**(목표 아님) | 불특정 다수 pack 거래소 = 현재 목표에서 제외 |

> **핵심 차이**: B brief는 "어디까지 열지"의 4단 계단이었고, 본 문서는 **"무엇을 만들지"의 2개 제품**으로 재정의. team이 1급 트랙으로 승격됐고, marketplace는 제품 목표에서 명시적으로 빠짐.

---

## 3. GO / DEFER / BLOCK 표 (갱신안)

| 항목 | 판정 | 근거 |
|---|---|---|
| **트랙 1 개인용 (private/local)** | **GO** (먼저 완성) | 현 안전모델로 커버. 기본 제품 |
| **트랙 1 GitHub 공개 옵션** | **조건부 GO (fail-closed dry-run 완료)** | redaction 체크리스트 전수 통과 + source pointer dirty/unknown→publish BLOCK(builder selftest GATE=GO) + 게이트2 owner 수동승인. 자동 sanitizer/치환·실 push는 HOLD (→ 작업 B / TRACK1_FAILCLOSED_PUBLISH_GUARD) |
| **트랙 2 팀 유료 (shared_with+access control)** | **DEFER → 단계적 GO** | access control 3종(엔진/격리/위임) 구현 선행 (→ 작업 C) |
| **트랙 2 billing/구독** | **DEFER** | 구독/결제 메커니즘 설계 필요. entitlement enum은 HOLD |
| **불특정 다수 marketplace** | **BLOCK** | 현재 목표 아님. public paid circulation·판매자/구매자/정산 구조 제외 |
| **release_mode/license/entitlement enum 확정** | **HOLD** | 데스크탑 publishing UI 앱 소스 부재(작업 A 실측). 확정 금지 |
| **우리 시스템/운영자의 OpenCrab store 자동 write/apply/ingest** | **HOLD** | [PRODUCTION_HOLD_BOUNDARY](OPENBINGGU_PRODUCTION_HOLD_BOUNDARY.md) 조건 1~7 미충족. ※ OpenCrab은 가입자가 자기 pack을 자기 의지로 올리는 곳 — **사용자 주도 자발 업로드는 별개**(동일 fail-closed gate 적용 시 1차 배포 흐름 포함 가능, [FIRST_RELEASE_GITHUB_MCP_DESIGN](OPENBINGGU_FIRST_RELEASE_GITHUB_MCP_DESIGN.md) §2-4) |

---

## 4. 트랙별 범위·차이·선행조건 요약

| 구분 | 트랙 1 개인용 | 트랙 2 팀 유료 |
|---|---|---|
| 데이터 경계 | owner+AI 내부(외부 0, GitHub 공개는 명시 옵션) | owner→지정 팀원 read 공유 |
| 사용자 수 | 1인 | 팀(다수) |
| 과금 | 무료/오픈소스 | 유료(구독/결제) |
| 핵심 선행조건 | 현 안전모델 유지 + (공개 시) redaction 체크리스트 | access control 3종 + team invite/role + billing |
| 위험 | 낮음(공개 시 redaction 누락만 관리) | 중간(권한위임·revoke·격리·결제분쟁) |
| 현재 판정 | GO | DEFER |

---

## 5. 현재 보류(HOLD/BLOCK) 명시 분리

- **BLOCK(제품 목표 아님)**: 불특정 다수 pack marketplace · public paid pack circulation · 유료 pack 판매자/구매자/정산 구조.
- **HOLD(확정 금지)**: release_mode/license/entitlement enum · production/OpenCrab/store write · apply/ingest/merge/push/v09/ARMED.

---

## 6. 다음 스텝 GO 후보

1. **트랙 1 개인용 기준 확정 + GitHub 공개 체크리스트** (작업 B) — enum 무관, 즉시 가능.
2. **트랙 2 팀 유료 최소 요건 + access control 3종 후보** (작업 C) — 설계 후보까지.
3. **문서 정렬 영향 검토** (작업 D) — 기존 team/public/marketplace 표현 → 2트랙 후보표.

## 7. 안전 확인

docs 1개 write만. production·OpenCrab/store/DB/opencrab_data/server·apply/ingest/merge/push·v09/ARMED·bid-engine·실자료 외부전송·raw PII/secret 출력 0. enum 확정 0. 운영 store mtime 불변.
