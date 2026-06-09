# BingguPack Phase 2 — Local Persistence 결과 + v0.2.0-rc1 계획 (2026-06-09)

> 설계: `OPENBINGGU_PHASE2_LOCAL_PERSISTENCE_DESIGN.md`. 결과: Phase 2-A(저장 흐름 selftest) + Phase 2-B(staging 재독 E2E).
> ⚠️ 전부 **synthetic / temp HOME / read-only(재독)**. 코드는 **작업트리에만** 존재, **공개 RC 미반영**(push 0). write opt-in 기능이라 공개 승격은 별도 GO.
> 표현 규칙: "100% 완성/보장/실유출 0" 금지. operating_store_unchanged·raw_leak=0·temp/synthetic 구분.

## 1. Phase 2-A 목적
사용자가 BingguPack 설치 후 **자기 로컬(OPENBINGGU_HOME)에 candidate graph를 안전하게 누적 저장**하는 흐름을 synthetic으로 검증. 운영 store·실 홈 write 0, candidate-only(promotion_allowed=0), confirmed/upload/push 0.

## 2. 구현 파일 (작업트리, push 0)
| 파일 | 역할 |
|---|---|
| `scripts/openbinggu_phase2_local_persistence_selftest.py` | 저장 흐름 selftest P1~P11. 기존 `StagingDB`·`staging_apply`·`base_pack` 무수정 재사용 + HOME 정책·multi-user·write OFF·emergency 래퍼 |
| `scripts/openbinggu_phase2_staging_reread_e2e.py` | 저장된 candidate를 **read-only**(SQLite `mode=ro`)로 재독하는 E2E E0~E9 |

## 3. Phase 2-A selftest 결과 — **11/11 PASS · GATE=GO**
| # | 항목 | 결과 |
|---|---|---|
| P1 | clean install staging apply+read-back | ✅ |
| P2 | operating_store_unchanged | ✅ |
| P3 | raw_leak=0 (경로 미출력) | ✅ |
| P4 | backup 실패 차단(`backup_create_failed`) | ✅ |
| P5 | checksum mismatch rollback | ✅ |
| P6 | duplicate 정규화 우회 차단(`duplicate_already_applied`) | ✅ |
| P7 | rollback snapshot 원복(before==rollback checksum) | ✅ |
| P8 | traversal / repo 내부 HOME 차단(`invalid_user_id`/`home_inside_repo_forbidden`) | ✅ |
| P9 | multi-user isolation(user_id 물리 분리) | ✅ |
| P10 | emergency stop BLOCK(`emergency_stopped`) | ✅ |
| P11 | write OFF 기본 BLOCK(`write_disabled`) | ✅ |

## 4. Phase 2-B staging 재독 E2E 결과 — **10/10 PASS · GATE=GO**
| # | 항목 | 결과 |
|---|---|---|
| E0 | 사전 저장(user_a/user_b 격리) | ✅ |
| E1 | node/edge/evidence read-back | ✅ |
| E2 | candidate=1 (node+edge) | ✅ |
| E3 | promotion_allowed=0 | ✅ |
| E4 | evidence_refs 보존 | ✅ |
| E5 | state=active | ✅ |
| E6 | **read-only 연결 write 차단**(`mode=ro` → OperationalError) | ✅ |
| E7 | user_id 격리(독립 재독) | ✅ |
| E8 | raw_leak=0 | ✅ |
| E9 | operating_store_unchanged | ✅ |

## 5. 안전 불변 (실측)
- **operating_store_unchanged=True** (OPERATING_PATHS = env override + temp dummy, 운영 localcrab_index/user_graph/_graph_merge mtime 불변)
- **raw_leak=0** (id·count·hash만, 실 홈/repo/사용자 절대경로 needle 0)
- **실 홈 write 0** (temp HOME만 사용, OS 표준 데이터 디렉토리의 binggupack 미생성 확인)
- **confirmed=0 · applied(운영)=0 · upload=0 · push=0 · neo4j=0**
- doctor 작업트리 회귀 **11/11 GATE=GO** (Phase 2 코드 추가 영향 0)

## 6. 교훈
- **반복 fixture는 케이스별 DB 격리 또는 unique id 필요.** Phase 2-A 1차 P5/P6 FAIL 원인 = `base_pack`의 node_id가 전부 `n1` 고정인데 같은 `local` DB를 케이스 간 공유 → 누적 상태에서 PRIMARY KEY 충돌(`IntegrityError`)이 checksum/duplicate 판정보다 먼저 발생. 격리(새 temp HOME)에선 정상. → 각 케이스 별도 user_id(=별도 DB)로 격리해 해결(staging_write_selftest 원본의 "케이스별 새 DB" 패턴 정합). 엔진(staging_apply)은 처음부터 정상.
- WAL backup/rollback = checkpoint(TRUNCATE) 후 snapshot + 복원 시 -wal/-shm 삭제(Phase 1 real_staging 교훈 재확인).
- read-only 보장은 SQLite `file:...?mode=ro` URI + write 시도 차단 실증(E6)으로 검증.

## 7. 공개 RC 미반영 (현재)
- Phase 2 코드는 **작업트리에만**. 공개 RC(github.com/darkjokee-arch/binggupack, v0.1.0-rc1)는 **read/dry-run 5도구 유지**, write 기능 0.
- 공개 승격은 v0.2.0-rc1 계획(§8) + 별도 owner GO.

## 8. v0.2.0-rc1 반영 계획
| 조건 | 정책 |
|---|---|
| **write 기본 OFF** | `write_enabled`/`personal_apply_allowed` 미설정 = OFF. 명시 opt-in 전 staging write 0. |
| **OPENBINGGU_HOME 필요** | write 사용 시 HOME 필수(미설정 시 안내+거부). repo 내부 HOME 거부. |
| **MCP write 도구 미노출 유지** | 공개 MCP는 **read/dry-run 5도구 그대로**. staging write는 MCP 노출 **안 함**(CLI opt-in only). write 도구 MCP 노출은 별도 중대결정. |
| **CLI opt-in only** | staging write는 명시 CLI 명령 + write_enabled로만. 자동/daemon 0(Phase 6). |
| **clean install selftest 통과 의무** | 릴리스 전 clean install(새 폴더)에서 P1~P11 + E0~E9 GATE=GO 재현. |
| **README 표현** | "local persistence (candidate-only, opt-in, write 기본 OFF)" 명시. confirmed/promote/apply는 별도(HOLD). |
| **버전 경계** | v0.1.0-rc1 = read/dry-run. v0.2.0-rc1 = +로컬 candidate 저장(opt-in). confirmed/OpenCrab finalize는 v0.3+/Phase 4·5. |

**반영 절차(별도 GO)**: ① Phase 2 코드 2파일을 RC scripts에 복사(개인경로 0 재확인) → ② clean install 시뮬에서 selftest+E2E GATE=GO → ③ README/INSTALL v0.2.0-rc1 범위 문구 + write opt-in 안내 → ④ doctor --tree CLEAN → ⑤ owner GO 후 commit/push + tag v0.2.0-rc1.

## 9. 계속 HOLD
공개 RC 반영(별도 GO) · confirmed apply/promote · staging→운영 승격 · OpenCrab upload/ingest/finalize · Neo4j start/add · 운영 store write · GitHub push(RC) · marketplace · team paid · enum(release_mode/entitlement) · fix5(enforce_access).
