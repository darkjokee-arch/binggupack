> OpenBinggu is the legacy/internal codename for BingguPack.

# OpenBinggu 작업 B — 개인용(트랙1) 기준 정렬 + GitHub 공개 대비 (DESIGN / 후보 ONLY)

> **상태라인(표준):** `marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)`
>
> **§3 GitHub 공개 게이트 갱신(2026-06-08)**: source pointer dirty/unknown → publish BLOCK이 pack builder에 반영됨(selftest GATE=GO/EXIT=0). 정교 자동 sanitizer/치환은 HOLD. 실 GitHub push는 owner 승인 전 HOLD. 상세 [TRACK1_FAILCLOSED_PUBLISH_GUARD_DESIGN](OPENBINGGU_TRACK1_FAILCLOSED_PUBLISH_GUARD_DESIGN.md).
>
> **상태: DESIGN/후보 ONLY · enum 확정 아님 · 실수정 0.**
> 본 문서는 신규 docs 1개 Write만. 기존 docs/scripts/fixture 수정 0 · production/OpenCrab/store/DB/server/bid-engine write 0 · apply/ingest/merge/push/v09/ARMED 0 · 실자료 외부전송 0 · raw PII/secret 출력 0.
> release_mode/license/entitlement enum 확정 금지(라이선스는 "표기 위치"만 다룸).
> 검증 표현 규칙: "production 보장"·"보안 완성" 금지 → "현재 fixture/temp 기준 추가 노출 미검출"만 사용.
>
> - 작성일: 2026-06-08
> - scope: project:openbinggu / 트랙1 개인용(private/local)
> - 상위: [PRODUCT_DIRECTION_TWO_TRACK](OPENBINGGU_PRODUCT_DIRECTION_TWO_TRACK.md) §1 트랙1 · §3 "트랙1 GitHub 공개 옵션 = 조건부 GO"
> - 참조: [PUBLIC_RELEASE_CHECKLIST](OPENBINGGU_PUBLIC_RELEASE_CHECKLIST.md) · [PUBLIC_RELEASE_POLICY](OPENBINGGU_PUBLIC_RELEASE_POLICY.md) · [PACK_CONTRACT](OPENBINGGU_PACK_CONTRACT.md) · [REAL_PACK_EXTERNAL_TRANSMISSION_POLICY](OPENBINGGU_REAL_PACK_EXTERNAL_TRANSMISSION_POLICY.md) · [SCOPE_ENVELOPE_DRYRUN_RESULT](OPENBINGGU_SCOPE_ENVELOPE_DRYRUN_RESULT.md)

---

## 0. 한 줄

**트랙1 개인용 = private/local 동작이 기본 제품 기준.** 그 위에 "개인이 자기 pack을 GitHub 오픈소스로 공개하는 옵션"을 얹되, **공개는 reader가 pack 콘텐츠를 외부에 올리는 것이 아니라 framework/skeleton + (선택 시) sanitized pack을 공개 저장소에 두는 것**이며, 공개 전 redaction·식별자 제거·source pointer 마스킹·secret/PII 0을 전수 통과해야 한다.

---

## 1. ⚠️ 두 개념 분리 — "GitHub 공개"(작업 B) ≠ "외부 LLM 송신"(C public, DEFER)

혼동 시 안전모델이 무너지므로 먼저 못 박는다.

| 구분 | GitHub 공개 (본 작업 B, 트랙1 옵션) | 외부 LLM 송신 (C public, **DEFER**) |
|---|---|---|
| 무엇이 나가나 | 정적 저장소 파일(코드/문서/템플릿/+선택 sanitized pack) | 실 pack claim 문장·source pointer가 제3자 LLM 서비스로 송신 |
| 데이터 흐름 | owner가 의도적으로 1회 push, 정적·검증 가능 | 런타임 송신, 학습/보존 정책 제3자 의존 |
| 위험 성격 | 누락된 raw/식별자가 영구 공개됨(되돌리기 어려움) | 데이터가 제3자 서비스에 남음 |
| 본 문서 판정 | **조건부 GO** (체크리스트 전수 통과 시) | **DEFER** — 본 문서 범위 밖 ([REAL_PACK_EXTERNAL_TRANSMISSION_POLICY] §6 절대 HOLD) |

> 핵심: 본 문서는 **"공개 저장소에 무엇이 들어가도 되는가"**만 다룬다. 실 pack을 외부 LLM에 보내는 송신 결정은 별개 중대결정으로 계속 DEFER/HOLD.

---

## 2. 개인용 버전 기본 기준 (private/local 동작 조건 · 안전 불변식)

트랙1 개인용은 **새 기능 추가 없이 현 안전모델을 "기본 제품 기준"으로 고정**한다. (DIRECTION_TWO_TRACK §3: 현 안전모델로 커버.)

### 2-1. private/local 동작 조건 (pack이 owner+AI 내부에서만 동작)
- **데이터 경계**: pack은 `owner + AI` 내부에서만 read/동작. 외부 reader·외부 서비스 전송 0 (DIRECTION §4 트랙1 데이터 경계 = owner+AI 내부).
- **user_root 일치**: pack의 `owner` = `user_root` = `user_namespace` 일관. 불일치 시 STOP (참조: SCOPE_ENVELOPE_DRYRUN `owner_user_root_consistent`).
- **cross-root 차단**: 다른 user_root의 evidence 혼입 시 STOP (`no_cross_root`).
- **deny-by-default 접근**: visibility/envelope은 표식일 뿐, 실제 read 허용은 reader_permission + session delegation + 범위 강제(`read_allowed`)가 결정. 개인용 기본은 owner 본인 grant만 존재.

### 2-2. 안전 불변식 (개인용 기본에서 항상 참이어야 함)
| # | 불변식 | 위반 시 | 근거 |
|---|---|---|---|
| I1 | `candidate-first` — 수집/생성물은 전부 candidate (candidate_all_true) | 자동 승격 사고 | PACK_CONTRACT, SCOPE_ENVELOPE |
| I2 | `promotion_allowed = false` (promotion_all_false) | STOP | PACK_CONTRACT §3 hard_defaults |
| I3 | `production_write_allowed / opencrab_ingest_allowed / github_publish_allowed` 기본 false | STOP | PACK_CONTRACT §3 |
| I4 | operating store mtime 불변 (`_graph_merge.yaml`·`user_graph.yaml`·`localcrab_index.sqlite`) | 운영 오염 | PACK_CONTRACT §6 |
| I5 | redaction residual 0 (독립 scanner `scan_residual_pii`) | STOP, raw 미기재 | watcher_batch_m1 |
| I6 | cross-root evidence 혼입 0 | STOP | SCOPE_ENVELOPE |
| I7 | enum(release_mode/license/entitlement) 미확정 — 값 채우지 않음(상태만 검사) | 추정 확정 금지 | DIRECTION §3 HOLD |

> **개인용 GO 의미**: 위 동작 조건 + I1~I7이 현재 dry-run/fixture/synthetic 기준 유지됨. production write·OpenCrab 연동·실 pack 외부전송은 트랙1 범위에서도 계속 HOLD.

---

## 3. GitHub 공개 대비 체크리스트 (공개 전 전수 통과 항목)

> 하나라도 FAIL이면 공개 금지. 본 문서는 기준 정의만 — 실제 스캔/공개 실행 0.
> 기존 [PUBLIC_RELEASE_CHECKLIST]를 트랙1 개인용 관점으로 **확장·정렬**한 것(기존 파일 수정 0, 본 문서에 신규 기술).

### 3-A. 저장소 구성 (무엇을 공개하나)
- [ ] 공개 대상 = framework/skeleton (scripts·validators·schema·templates·docs·synthetic fixtures·toy examples) — PUBLIC_RELEASE_POLICY §1.
- [ ] 작성자 실 데이터 미포함: production_graph·sqlite/db·실 reports/reviews/captures/evidence_index·reingest_pack_draft 원본 0.
- [ ] (개인이 자기 pack도 공개하려는 경우) 해당 pack은 **§3-C sanitization 전수 통과한 sanitized pack만** — 원본/raw 동봉 금지.

### 3-B. secret / 식별자 / 경로 0 검증
- [ ] `.env` / token / key / credential / cookie / private_key 0.
- [ ] secret scan PASS — repo 전체 rglob + secret 정규식(scan_kv: `api_key|token|secret|password` 형태, scan_aws: `AKIA…`) → 0건. 검출 시 **존재·길이만** 보고(raw 통째 출력 금지).
- [ ] PII 0 — `scan_residual_pii` 형태(주민/휴대폰/유선/email/AWS키/kv-secret) 잔존 0건.
- [ ] 실제 경로/사용자명/프로젝트명 노출 0 (예: `C:\Users\<id>\…`, 사내 프로젝트명).

### 3-C. pack sanitization (개인 pack을 공개할 때만)
- [ ] **raw evidence 0** — evidence_chunk/evidence_index에 원문(raw) 미포함. claim/요약만, 원문 대신 hash_reference 형태 권장.
- [x] **source pointer 판정(fail-closed)** — pack builder(`watcher_pack_builder_m0`)가 모든 source pointer를 `clean | dirty | unknown` 판정(`source_pointer_scan`/`source_pointer_mask`). **dirty(Windows 절대경로/`file://`·UNC·비공개 unix path·localhost·내부IP·내부도메인) 또는 unknown(빈값·토큰)이 1건이라도 있으면 publish BLOCK** → selftest GATE=GO/EXIT=0(2026-06-08). ⚠️ **정교한 자동 마스킹/치환 로직은 HOLD**(현재는 "clean 아니면 차단"만 / 마스킹 후 공개 정책 미결, raw 경로 미출력·라벨·count만).
- [ ] 식별자 제거 — owner 실명/실 user_root·내부 식별자가 공개 pack에 남지 않음(synthetic 식별자로 치환).
- [ ] redaction **재실행**(공개 시점 기준, 이전 결과 재사용 금지 — REAL_PACK_TRANSMISSION §3 원칙 차용).

### 3-D. .gitignore / 구조 가드
- [ ] `.gitignore` 적용 확인 — `.env`·`*.sqlite`·`*.db`·`localbinggu_production_graph.yaml`·`reingest_pack_draft/`·`reports/`·`reviews/`·`captures/`·`packs/private/`·`*_secret*`·`*_token*`·`credentials*`·`private_key*` (PUBLIC_RELEASE_POLICY §5).
- [ ] synthetic/toy만 추적 경로(`tests/fixtures/synthetic/`·`examples/toy_project/`)에 존재.
- [ ] `git check-ignore`로 ignore 매칭 누락 파일 검증.

### 3-E. 문서 / 라이선스 (enum HOLD)
- [ ] README에 "작성자 private data 미포함" 문구(EN/KO) — PUBLIC_RELEASE_POLICY §7.
- [ ] **라이선스는 "표기 위치"만 확보**: repo 루트 `LICENSE` 파일 + README 배지 위치 + (공개 pack 시) manifest `license` 필드 위치 자리만 마련. **license enum 값 확정·기재는 HOLD** (DIRECTION §3 enum HOLD, 데스크탑 publishing UI 앱 소스 실측 전 금지).

> **핵심 항목 수: 5개 그룹(3-A~3-E), 세부 체크 18개.**

---

## 4. private/local fixture 보강 후보 (후보 ONLY · 실제 생성·수정·실행 금지)

> 아래는 "추가하면 좋은 fixture/케이스" 후보 + 갱신 후 기대 PASS/FAIL 기준. **실제 fixture 생성·수정·실행은 별도 GO**. 현재 SCOPE_ENVELOPE_DRYRUN은 정상 3 + negative 12 구조.

### 4-1. 공개 sanitization negative 후보 (공개 저장소 유입 차단 검증)
| 후보 case | 종류 | 기대 | 실패해야 할 check(의도) |
|---|---|---|---|
| `publish_raw_evidence_bad` | negative | STOP/FAIL | raw evidence 원문 포함 → redaction_no_residual / raw_allowed_false |
| `publish_source_pointer_abspath_bad` | negative | STOP/FAIL | source pointer가 `C:\Users\…` 절대경로 노출 → (신규) source_pointer_masked |
| `publish_env_secret_bad` | negative | STOP/FAIL | `.env`/kv-secret 형태 포함 → scan_residual_pii(scan_kv) |
| `publish_real_userroot_bad` | negative | STOP/FAIL | 실 user_root/실명 식별자 잔존 → owner_user_root_consistent(synthetic 아님) |
| `publish_gitignore_miss_bad` | negative | STOP/FAIL | `*.sqlite`/production_graph가 추적 경로에 존재 → gitignore_match |

### 4-2. 공개 정상(통과) 후보
| 후보 case | 종류 | 기대 | 통과 조건 |
|---|---|---|---|
| `publish_skeleton_only_ok` | 정상 | PASS | framework/skeleton만, 데이터 0 → 모든 check PASS, failed 0 |
| `publish_sanitized_pack_ok` | 정상 | PASS | sanitized pack(synthetic 식별자·hash_reference·마스킹 경로) → redaction residual 0 + source_pointer_masked + raw_allowed_false |
| `publish_toy_example_ok` | 정상 | PASS | toy_project만 → 과검출 0 |

### 4-3. 신규 validator check 후보 (현 dryrun에 없는 것)
- `source_pointer_masked` — pack 내 모든 source pointer가 비공개 절대경로/사내 URL 미노출(현 builder 미구현 → 가장 우선 보강).
- `gitignore_match` — 공개 전 추적 파일이 ignore 정책과 충돌 없음(정적 목록 대조).
- `synthetic_identity_only` — 공개 pack의 owner/user_root가 synthetic 식별자 집합에 속함.

### 4-4. 갱신 후 PASS/FAIL 판정 기준 (보강 시)
- 정상 case = failed check 0 (과검출 0).
- negative case = **의도한 check에서만** STOP/FAIL (다른 정상 항목 영향 0).
- redaction 표현: 정상 case는 "현재 fixture/temp 기준 추가 노출 미검출"만 기재. raw 값 미기재.
- GATE=GO 조건 = 전 case expected == actual, mismatch 0.

---

## 5. 남은 HOLD / 다음 GO 필요 지점

- **HOLD(확정 금지)**: release_mode/license/entitlement enum(§3-E 표기 위치만) · production/OpenCrab/store write · apply/ingest/merge/push/v09/ARMED.
- **DEFER(범위 밖)**: 실 pack 외부 LLM 송신(C public) — 본 문서와 별개 중대결정.
- **gap 진행(2026-06-08)**: pack builder의 source pointer **판정 + dirty/unknown→publish BLOCK 연결은 구현 완료**(selftest GATE=GO/EXIT=0). 남은 gap = **정교한 자동 마스킹/치환 로직(HOLD)** — 현재는 "clean 아니면 차단"만. 마스킹 후 공개 허용 여부는 정책 결정(별도 GO).
- **다음 GO 후보(전부 별도 GO)**:
  1. source pointer 마스킹 로직 + `source_pointer_masked` validator check 구현(공개 전 필수).
  2. §4 공개 sanitization fixture 8건 실제 생성 + dryrun 확장 + selftest 재실행.
  3. license enum 확정 — 데스크탑 publishing UI 앱 소스 실측(작업 A) 완료 후.
  4. 실제 GitHub repo 생성·push는 위 1~2 통과 + owner 명시 승인 후 별도 결정.

---

## 6. 안전 확인

신규 docs 1개 write만. 기존 docs/scripts/fixture 수정 0 · production·OpenCrab/store/DB/opencrab_data/server·apply/ingest/merge/push·v09/ARMED·bid-engine·실자료 외부전송·raw PII/secret 출력 0. enum 확정 0. 조사는 Read/Grep/Glob만 사용. 운영 store mtime 불변(write 미발생).
