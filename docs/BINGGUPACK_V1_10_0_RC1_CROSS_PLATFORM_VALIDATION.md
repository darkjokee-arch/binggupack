# BingguPack v1.10.0-rc.1 — Cross-Platform Validation

> 2026-06-24. RC branch `feat/installable-mcp-v1.10.0-rc.1` 의 clean install 을 플랫폼별로 검증.
> **main merge / stable tag·release 생성 안 함** (HELD). 이 문서는 검증 기록 전용.

## 1. 검증 매트릭스

| 환경 | clean install | smoke 10/10 | installer dry-run | installer apply | 비고 |
| - | - | - | - | - | - |
| **Windows** (이 머신, MINGW64) | ✅ in-place + clean clone | ✅ PASS | ✅ PASS | ✅ apply → `claude mcp get` **Connected** → remove 원복 | 운영 2개 무손상 |
| **Linux** (docker `python:3.12`, x86_64) | ✅ `git clone -b v1.10.0-rc.1` | ✅ **PASS** | ✅ PASS | ⏸ NOT_AVAILABLE (컨테이너에 `claude` CLI 없음) | POSIX 실증·image pull 0(로컬 보유) |
| **WSL Ubuntu** | — | — | — | — | **NOT_AVAILABLE** — Ubuntu 배포판 미설치(docker-desktop만). PASS 처리 안 함 |
| **macOS** | — | — | — | — | **NOT_AVAILABLE** — Windows 머신. PASS 처리 안 함 |

- Linux 검증으로 POSIX clean install 이 실증됨(WSL Ubuntu 의 직접 대체는 아니나 동일 POSIX 경로·런처 `python3` 동작 확인).
- 두 환경(Windows/Linux) 모두 `tag v1.10.0-rc.1` checkout → smoke 통과.

## 2. Claude restart 후 MCP tool exposure
- **HELD (이 세션 불가).** MCP 도구는 세션 시작 시 고정 + AI(현 세션)는 Claude Code 재시작 불가.
- 확인된 범위: `install_claude_mcp.py --apply` → `claude mcp get openbinggu-local-sandbox` **Status: √ Connected** (Windows clean clone). 즉 등록·연결은 검증됨. **세션 tool 목록 노출 + 실 MCP 호출 8도구**는 owner 재시작 후 확인 필요.
- 상태: `MCP_CLEAN_INSTALL_RESTART_REQUIRED`.

## 3. smoke_test 결과 (Windows + Linux 공통)
1 selftest · 2 capture_classify · 3 capture_preview(nothing_saved) · 4 pack_build dry-run · 5 pack_validate · 6 publish_guard_dryrun · 7 consumer_smoke · 8 save_candidate dry-run(write0) · 9 **save actual → G4_no_auto BLOCK** · 10 operating ledger write 0 → **10/10 PASS**.

## 4. 안전/무결성
- `G4_no_auto` 유지 (AI/reader actor durable save 불가) — 양 플랫폼 확인.
- `BINGGU_HOME` 격리로 운영 `~/.binggupack` 미접촉. Linux 는 컨테이너 격리(Windows FS 미접촉).
- actual API call 0 · source fetch 0 · production write 0 · OpenCrab ingest 0 · insane-search 외부 0 · upload 0 · private/secret 0.

## 5. 발견 결함
- **없음.** 모든 가용 환경에서 PASS, 미가용 환경은 NOT_AVAILABLE 로 명확 기록(PASS 처리 안 함).

## 6. stable(`v1.10.0`) 승격 가능 여부
| 조건 | 상태 |
| - | - |
| Windows clean clone PASS | ✅ |
| WSL Ubuntu smoke PASS 또는 NOT_AVAILABLE 명확 | ✅ NOT_AVAILABLE 명확 (Linux docker 로 POSIX 보강) |
| macOS smoke PASS 또는 NOT_AVAILABLE 명확 | ✅ NOT_AVAILABLE 명확 |
| Claude restart 후 MCP tool exposure PASS | ⏸ **HELD** (owner 재시작 필요) |
| smoke 10/10 PASS | ✅ (Windows + Linux) |
| installer dry-run/apply PASS where available | ✅ |
| G4_no_auto 유지 / real home 0 / API·fetch·ingest 0 | ✅ |

→ **판정: `V1_10_0_RC1_PARTIAL_VALIDATION_DOCS_UPDATED`**. cross-platform 은 Windows+Linux 실증 + WSL/macOS NOT_AVAILABLE 명확 기록. 유일한 잔여 = **Claude restart 후 실 tool exposure**(구조상 owner 재시작 영역). 이 1건 확인 시 stable 승격 후보.
- **stable release 는 이번에도 생성하지 않음** (`MAIN_MERGE_HELD_FOR_STABLE_DECISION`).

## 7. 상태명
`BINGGUPACK_REPO_RECONCILED` · `MCP_INSTALLABLE_PACKAGE_READY` · `MCP_CLEAN_INSTALL_E2E_PASS` · `G4_NO_AUTO_CONFIRMED` · `REAL_HOME_UNCHANGED` · `V1_10_0_RC1_PRERELEASE_CREATED` · `MAIN_MERGE_HELD_FOR_STABLE_DECISION` · `V1_10_0_RC1_PARTIAL_VALIDATION_DOCS_UPDATED`.
