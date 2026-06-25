# Changelog — BingguPack

## v1.10.0 — Installable MCP Package (stable) (2026-06-25)

`v1.10.0-rc.1` 을 stable 로 승격. RC 기능은 그대로이며 cross-platform 검증과 MCP tool exposure 검증을 통과해 stable line(main)으로 병합했다.

### Added (RC 대비)
- `.github/workflows/mcp-cross-platform-install.yml` — ubuntu/macos/windows 3-OS clean-install CI. 각 job: tag clone → `smoke_test.py` 10/10 → installer dry-run → operating-name protection refusal. **전 OS PASS** (run 28138244904).
- `docs/BINGGUPACK_V1_10_0_RC1_CROSS_PLATFORM_VALIDATION.md` — cross-platform 검증 기록.

### Verified (stable gate)
- `MCP_TOOL_EXPOSURE_PASS` — 재시작 세션에서 sandbox MCP(`openbinggu-local-sandbox`, `BINGGU_HOME` 격리) 8도구 live 노출 + 전부 `ALLOW`.
- `save_candidate(dry_run=false)` actual save → `G4_no_auto BLOCK` (`executed_write=false`, `ledger=temp_only`).
- 운영 home `ledger.sqlite`/`-wal` 불변, production write 0, OpenCrab ingest 0, G4 우회 0.

RC 의 전체 기능 목록은 아래 `v1.10.0-rc.1` 항목 참조.

## v1.10.0-rc.1 — Installable MCP Package and Workflow Factory (2026-06-24)

신규 사용자가 **clone만으로 BingguPack MCP를 설치**할 수 있게 설치 경험을 완성.

### Added
- `scripts/install_claude_mcp.py` — `claude mcp add` 헬퍼. repo root 자동 감지, server.py 경로 계산, `BINGGU_HOME`/`OPENCRAB_HOME`/`XDG_CACHE_HOME` 주입, `--dry-run`/`--apply`/`--name`/`--home`/`--sandbox`/`--force`, 동일이름 가드, **운영 엔트리(`openbinggu-local`) 보호**(거부), Windows `claude.cmd` shim 처리(`shutil.which`).
- `scripts/smoke_test.py` — clone 직후 오프라인 검증. 8도구 + save gate(G4_no_auto) + 운영 home 불변 = 10 checks. 실존 fixture(`examples/toy_project/`) 사용.
- `pyproject.toml` — 패키지 메타(version, stdlib-only).
- `docs/BINGGUPACK_MCP_CLEAN_INSTALL_E2E_TEST_REPORT.md` — clean install E2E 결과.

### Notes
- BingguPack MCP 서버는 **이 본체 repo(`darkjokee-arch/binggupack`)** 에서 설치된다(OpenCrab repo 아님).
- `BINGGU_HOME` 으로 sandbox/운영 home 분리. 미설정 시 OS별 `~/.binggupack`.
- AI/reader actor 의 실저장은 `G4_no_auto` 로 차단. 저장은 사람 actor 의 `SAVE n` 승인 게이트에서만.
- actual API collection 은 release requirement 가 아니다. insane-search 는 optional evidence discovery adapter. production write 는 기본 0.
- OpenCrab repo 의 `v1.8.1-rc.1` 작업은 임시 구현/검증본 → 본체 repo 로 정렬·이관 완료.

## v1.9.0 — 확정→폰/웹 자동 공유 + setup-cloud (2026-06-16)
## v1.8.0 — 똑똑한 뜻 분류 자동 켜짐 + 첫 설치 환경 점검 (2026-06-15)
## v1.7.0 / v1.6.1 / v1.6.0 / v1.5.x / v1.4.x — 이전 릴리스 (GitHub Releases 참조)
