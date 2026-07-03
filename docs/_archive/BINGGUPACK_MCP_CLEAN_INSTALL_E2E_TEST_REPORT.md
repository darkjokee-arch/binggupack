# BingguPack MCP — Clean Install E2E Test Report (본체 repo)

> 2026-06-24. 본체 repo `darkjokee-arch/binggupack` 기준으로 **clone → smoke → install → connected** 실측.
> target `v1.10.0-rc.1` (본체 최신 v1.9.0 → installable MCP + workflow factory = 기능 확장 minor bump).
> OpenCrab repo 의 `v1.8.1-rc.1` 작업(vendor 검증본)을 본체 repo 로 정렬·이관 완료.

## 1. 배경
- 본체 repo 는 `scripts/` 에 MCP 서버(`openbinggu_mcp_server.py`) + 런타임 모듈 풀세트를 이미 포함 → clone 만으로 서버가 따라온다.
- 부족했던 것: **설치 편의 도구**(installer/smoke) + 설치 문서 + 버전 메타. 이를 본체 스타일(flat `scripts/`)로 추가. OpenCrab식 `packages/` 중복 vendor 는 불필요(중복 0).

## 2. 추가물
- `scripts/install_claude_mcp.py` — `claude mcp add` 헬퍼. repo root 자동 감지, `BINGGU_HOME`/`OPENCRAB_HOME`/`XDG_CACHE_HOME` 주입, `--dry-run`/`--apply`/`--name`/`--home`/`--sandbox`/`--force`, 동일이름 가드, **운영 엔트리 `openbinggu-local` 보호(거부)**, Windows `claude.cmd` shim(`shutil.which`).
- `scripts/smoke_test.py` — clone 직후 오프라인 검증. **실존 fixture**(`examples/toy_project/`, `expected/toy_pack_summary.json`) 사용(이전 phantom path 의존 제거).
- `pyproject.toml`(version 1.10.0rc1, deps=[]) · `CHANGELOG.md` · `INSTALL.md`/`README.md` 설치 절차.

## 3. 테스트 결과

### 3-1. smoke_test.py (in-place 본체 + clean clone)
| # | check | in-place | clean clone |
| - | - | - | - |
| 1 | selftest ALLOW | PASS | PASS |
| 2 | capture_classify ALLOW | PASS | PASS |
| 3 | capture_preview ALLOW · nothing_saved | PASS | PASS |
| 4 | pack_build dry-run ALLOW | PASS | PASS |
| 5 | pack_validate ALLOW (실존 fixture) | PASS | PASS |
| 6 | publish_guard_dryrun ALLOW | PASS | PASS |
| 7 | consumer_smoke ALLOW | PASS | PASS |
| 8 | save_candidate dry-run write0 | PASS | PASS |
| 9 | save actual → **G4_no_auto BLOCK** | PASS | PASS |
| 10 | operating ledger write 0 | PASS | PASS |

→ **10/10 PASS (양쪽).**

### 3-2. clean clone E2E
- `git clone -b feat/installable-mcp-v1.10.0-rc.1 --depth 1 darkjokee-arch/binggupack` → `C:\Users\PC\binggupack_repo_clean_install_test`.
- `smoke_test.py --home <clean home>` → **RESULT: PASS**.
- `install_claude_mcp.py --dry-run` → 정확한 `claude mcp add ... --serve <clean repo>` 명령 생성. PASS.
- `--apply --name openbinggu-cleantest-sandbox`(임시명) → 등록 성공 → `claude mcp get` → **Status: √ Connected**. PASS.
- `claude mcp remove` 원복 → cleantest 제거, **운영 `openbinggu-local`/`openbinggu-local-sandbox` 둘 다 Connected 유지**.

## 4. 안전/무결성
- AI/reader actor 실저장 `G4_no_auto` 차단 유지. 저장은 사람 `SAVE n` 승인 게이트에서만.
- `BINGGU_HOME` 격리로 운영 `~/.binggupack` 미접촉(OPERATING_PATHS mtime 불변).
- actual API call 0 · source fetch/network 0(git 제외) · insane-search 외부 0 · OpenCrab ingest 0 · production write 0 · upload script 0 · 기존 release/tag 삭제 0 · private/secret 이관 0.

## 5. 판정
- `BINGGUPACK_REPO_RECONCILED`
- `MCP_INSTALLABLE_PACKAGE_READY`
- `MCP_CLEAN_INSTALL_E2E_PASS`
- `G4_NO_AUTO_CONFIRMED`
- `REAL_HOME_UNCHANGED`
- (현재 세션 새 도구 노출은 재시작 후 — apply는 Connected 까지 확인됨: `MCP_CLEAN_INSTALL_RESTART_REQUIRED`)

## 6. 신규 사용자 설치
```bash
git clone https://github.com/darkjokee-arch/binggupack.git && cd binggupack && git checkout v1.10.0-rc.1
python scripts/smoke_test.py --home ./_binggu_test_home
python scripts/install_claude_mcp.py --sandbox --home ./_binggu_test_home --apply
# Claude Code 재시작 → claude mcp list
```

## 7. Merge 상태 — main merge HOLD (2026-06-24)

`v1.10.0-rc.1` 은 **prerelease** 이므로 main 에 merge 하지 않고 release candidate branch 로 유지한다.

- branch: `feat/installable-mcp-v1.10.0-rc.1` (origin/main 대비 **+5 commit / -0**, fast-forward 가능)
- diff: 7파일 / +346 줄, **전부 신규 추가**(기존 운영 코드 수정 0).
- prior stable: `v1.9.0` (main 그대로 보존) · new prerelease: `v1.10.0-rc.1`.
- release: https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0-rc.1 (prerelease=true, draft=false).

### stable 승격(`v1.10.0`) 전 남은 조건
1. WSL Ubuntu / macOS 등 **cross-platform clean install** 1회 이상 (현재 Windows + clean clone PASS).
2. 재시작 후 실제 MCP 도구 노출 확인(현 세션은 apply→Connected 까지만).
3. owner 의 stable 승격 결정(이후 main merge + `v1.10.0` tag/release).

### 상태명
`BINGGUPACK_REPO_RECONCILED` · `MCP_INSTALLABLE_PACKAGE_READY` · `MCP_CLEAN_INSTALL_E2E_PASS` · `G4_NO_AUTO_CONFIRMED` · `REAL_HOME_UNCHANGED` · `V1_10_0_RC1_PRERELEASE_CREATED` · `MAIN_MERGE_HELD_FOR_STABLE_DECISION`.
