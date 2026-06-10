> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# OpenBinggu 1차 배포 — GitHub + MCP 방식 설계 (DESIGN, A 초안)

> **상태: 1차 배포 설계 문서(2026-06-08). docs only · 코드 구현 0 · 실 repo/push 0 · production write 0.**
> 4CLI 토론 입력 A 초안. 결론·구현은 final 종합·별도 GO.
> HOLD: 실 GitHub push · OpenCrab write/apply/ingest/store · v09/ARMED · enum 확정 · team_paid · marketplace · sanitizer 자동치환.
> 표현 규칙: "production 보장"·"보안 완성" 금지 → "현재 fixture/temp 기준 추가 노출 미검출"만.

---

## 0. 한 줄

OpenBinggu 개인용 RC를 **GitHub 공개 + local MCP 서버** 형태로 1차 배포한다. 사용자는 repo를 받아 자기 MCP 클라이언트(Claude Code 등)에 등록하고, **자기 로컬 자료로 개인 pack을 만들고 검증**한다. **공개 repo에는 synthetic/example만** 들어가고 개인 데이터는 로컬에만 남는다. OpenCrab은 **각 사용자가 가입해서 자기 pack을 자기 의지로 업로드하는 곳**이며(우리가 데이터를 자동으로 쌓는 중앙 store 아님), **사용자 주도 업로드 경로는 1차 흐름에 포함 가능**하다. 다만 **우리 시스템/운영자가 OpenCrab store에 자동 write/apply/ingest 하는 경로는 계속 HOLD**다.

---

## 1. 왜 GitHub + MCP 인가 (1차 배포 적합성)

| 방식 | 1차 배포 적합성 | 사유 |
|---|---|---|
| **GitHub 공개 + local MCP** | **채택(후보)** | 우리 시스템의 운영 store 자동 write 0으로 배포 가능. 사용자가 로컬에서 실행, 개인 데이터 로컬 고정. 설치=clone+MCP config |
| **사용자 주도 OpenCrab 업로드** | **1차 포함 가능** | 가입자가 자기 pack을 자기 의지로 올림(우리가 쌓는 중앙 store 아님). GitHub 공개와 **동일 fail-closed gate** 적용 시 1차 흐름 포함 가능 |
| 우리 시스템/운영자의 OpenCrab store 자동 write/apply/ingest | **HOLD** | 운영 그래프 자동 오염 위험 → 사용자 자발 업로드와 구분, 계속 HOLD |

- GitHub + MCP는 "코드/스켈레톤 배포 + 로컬 실행"만으로 성립 → **우리 시스템이 운영 store에 자동 write 하지 않음**(HOLD 원칙 유지). 사용자가 자기 OpenCrab 계정에 자발적으로 올리는 것은 사용자 행위로, 우리 운영 write와 별개.
- MCP는 사용자가 이미 쓰는 AI 클라이언트에 도구를 붙이는 표준 → 별도 앱 설치 부담 낮음.

---

## 2. A. 1차 배포 구조

### 2-1. GitHub repo에 올리는 것 (공개 대상)
- framework/skeleton: `scripts/`(pack 빌드·검증·소비·publish guard), `opencrab/`·`server/` 중 **개인용 RC 실행에 필요한 코드만**(추후 분리), schema/validators.
- `docs/`: 설계·정책·체크리스트·README·install guide·MCP config 예시.
- `examples/toy_project/`: synthetic toy 프로젝트(개인 데이터 아님).
- `tests/fixtures/synthetic/`: 합성 fixture(공개 sanitization·portability 등).
- `LICENSE`(위치만), `.gitignore`(공개 제외 목록).

### 2-2. GitHub repo에 절대 안 올리는 것 (공개 제외)
- 작성자 실 그래프/DB/sqlite(`localcrab_index.sqlite`·`*_graph.yaml`·`*.db`).
- 실 reports/reviews/captures/evidence 원문·`reingest_pack_draft/` 원본.
- `.env`·token·key·credential·cookie·private_key.
- 실제 경로(`C:\Users\<id>\...`)·실명·사내 URL·내부 IP.
- bid-engine 등 무관 운영 코드/데이터.

### 2-3. pack 제공 방식
- 공개 repo의 pack은 **synthetic/example 중심**(toy_public_pack 등). 실 pack 미동봉.
- 개인 pack은 **각 사용자가 자기 로컬에서 생성** → 공개 repo로 흘러가지 않음(fail-closed 게이트 통과 + owner 승인 시에만 본인이 별도 공개).

### 2-4. OpenCrab 업로드 — 경로 구분
- **사용자 주도 업로드(1차 포함 가능)**: 가입자가 자기 pack을 자기 의지로 OpenCrab 계정에 올림. GitHub 공개와 **동일 fail-closed gate**(dirty/unknown→BLOCK·raw 차단·수동 승인) 통과 시에만. 우리가 강제로 쌓는 중앙 store 아님.
- **우리 시스템/운영자의 자동 store write/apply/ingest(HOLD)**: 운영 그래프 자동 연동·자동 적재는 1차 범위 밖. 별도 GO.

---

## 3. B. 사용자 흐름 (설치 → 사용 → 공개)

```
1. GitHub에서 repo 받기            git clone <REPO_URL>
2. MCP 설정에 추가                  MCP config 에 openbinggu local server 등록 (§MCP_EXPOSURE 후보)
3. selftest 실행                    4개 selftest GATE=GO 확인
4. 로컬 개인 pack 생성              자기 로컬 자료 → pack 빌드(candidate, 로컬에만 저장)
5. fail-closed 체크                 source pointer/redaction 게이트 → clean 만 공개 후보
   - dirty/unknown → 공개 BLOCK
   - raw PII/secret/private path 미출력(count/reason_code/source_pointer_id 만)
6. owner 수동 승인 후에만 공개      게이트 통과 + owner 1회 approve → (별도) 공개
```

- 4·5 단계 산출(개인 pack)은 **로컬 고정**. 공개 전까지 외부 유출 0.
- 5 단계 결과 표시는 **raw 미출력 원칙** 유지.
- 6 단계 "공개"는 **두 경로 모두 동일 fail-closed gate** 적용: ① GitHub 공개 push, ② 사용자 자기 OpenCrab 계정 업로드. 둘 다 dirty/unknown→BLOCK·raw 차단·**owner/user 1회 수동 승인 전 금지**.

---

## 4. 1차 배포 GO / BLOCK 조건 (요약, 상세는 체크리스트 문서)

- **GO 후보 조건**: selftest 4개 GATE=GO · dirty/unknown→BLOCK 동작 · 공개 제외 경로 확인 · secret/PII scan 기준 충족 · README/install/MCP config 예시 존재 · owner 수동 승인 단계 명시.
- **BLOCK 조건**: selftest 1개라도 GATE≠GO · 공개 repo에 실데이터/secret 유입 가능 · MCP가 위험 기능(write/apply/push) 노출 · raw 출력 발생 · owner 승인 없이 push 자동화.

---

## 4-2. 4CLI 토론 결과 반영 (2026-06-08, both_reject → 공개 GO 선결 gate)

> 본 A 초안은 4CLI 토론에서 **방향 GO / "지금 공개 GO" HOLD-REFINE**(judge=both_reject) 판정됨. 상세 [FIRST_RELEASE_4CLI_SYNTHESIS](OPENBINGGU_FIRST_RELEASE_4CLI_SYNTHESIS.md).
> 공개 push 전 선결 gate(전부 별도 GO, 현재 HOLD):
> - **보안(C)**: S1 git 히스토리 영속성(새 clean repo 시작·history purge 절차) · S2 공개 pack 디폴트 source pointer 미포함 · S3 MCP path TOCTOU 포함 격리 · S4 합성 fixture 통과≠실데이터 안전 · S5 신규코드(stub/doctor) 공격면 최소화.
> - **격리/효용(D)**: X1 bid-engine/인증서 등 타프로젝트 경로 deny(로컬 격리) · X2 OpenCrab 없이도 효용 보이는 toy end-to-end 시나리오(OpenCrab 통합은 2차 유지).
> RC = 1차 배포 *후보*이지 *공개 GO 후보* 아님.

## 5. HOLD / DEFER / BLOCK (불변)

- **HOLD**: 실 GitHub push(owner 승인) · OpenCrab write/apply/ingest/store · v09/ARMED · enum 확정 · sanitizer 자동치환 · whitelist 구현.
- **DEFER**: 팀 유료(트랙2).
- **HOLD(1차 포함 아님)**: 우리 시스템/운영자의 OpenCrab store **자동** write/apply/ingest. (사용자 주도 자발 업로드는 fail-closed gate 적용 시 1차 포함 가능 — §2-4)
- **BLOCK**: marketplace.

## 6. 안전

docs only. 코드·production·OpenCrab/store/DB·apply/ingest/merge/push·v09/ARMED·bid-engine·enum·team_paid·marketplace·sanitizer·raw 출력 0. operating store mtime 불변.
