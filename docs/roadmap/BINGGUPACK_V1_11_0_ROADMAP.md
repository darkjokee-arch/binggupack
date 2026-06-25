# BingguPack v1.11.0 — Roadmap / Scope

> 후보(candidate) 로드맵입니다. v1.11.0 stable release가 아니며, 모든 항목은 owner 승인 게이트를 따릅니다.
> Base: `v1.10.0` stable (`d15e6d6`) · Branch: `feat/v1.11.0-roadmap-ultra`.

## 목표 (Goals)

| # | 항목 | 상태 | 비고 |
|---|---|---|---|
| 1 | scripts 디렉토리 리팩토링 | **설계만 (BLOCKED)** | 116 스크립트 + selftest 경로 의존 → 대규모 이동 위험. [SCRIPTS_REFACTOR_PLAN](../refactor/SCRIPTS_REFACTOR_PLAN.md) |
| 2 | `binggupack/` 패키지 모듈화 | **설계만** | 현재 평면 구조(top-level `binggu.py` + `scripts/`). 단계적 wrapper 전략 |
| 3 | `smoke_test.py` / `install_claude_mcp.py` backward-compat wrapper 유지 | **불변 원칙** | 두 스크립트는 독립 실행(패키지 import 0) — 모듈화해도 entrypoint 보존 |
| 4 | interactive UX 옵션 | **설계만** | [INTERACTIVE_SAVE_GATE_DESIGN](../ux/INTERACTIVE_SAVE_GATE_DESIGN.md). 기본은 explicit confirm 유지 |
| 5 | pack/workflow lifecycle examples | **착수(예제 추가)** | [examples/pack_workflow](../../examples/pack_workflow/README.md) |
| 6 | tutorial / examples 추가 | **부분(Lane D)** | pack/workflow 예제로 시작 |
| 7 | PyPI / package distribution readiness | **점검 완료(NOT READY)** | [PYPI_READINESS](../packaging/PYPI_READINESS.md) |
| 8 | CI / regression matrix 유지 | **유지** | 3-OS CI + smoke 10/10 불변 |

## 비목표 (Non-goals)

- v1.11.0 stable release / tag 생성 (이번 사이클 범위 아님)
- 기존 public entrypoint(`scripts/smoke_test.py`, `scripts/install_claude_mcp.py`, `binggu.py`) 시그니처 변경
- OpenCrab Cloud ingest 활성화 / marketplace / paid workflow deployment
- AI autosave / `G4_no_auto` 완화

## 금지사항 (Hard constraints)

- v1.10.0 stable tag `d15e6d6` 이동 금지
- main 직접 작업 금지 (branch에서만)
- production write / 운영 ledger 접근 0
- PyPI publish / 비밀키·토큰 사용 0

## Release gate (v1.11.0이 stable이 되려면)

1. 기존 public command 전부 무결 (smoke 10/10, install dry-run, `binggu.py --selftest`)
2. MCP 8도구 exposure 유지 + `save_candidate(dry_run=false)` → `G4_no_auto BLOCK`
3. 3-OS CI PASS
4. external clean-clone verify PASS (v1.10.0과 동일 게이트)
5. backward-compat 검증 (모듈화 시 기존 경로 import/실행 호환)
6. owner 명시 승인 → tag/release 생성

## Rollback plan

- 모든 v1.11.0 작업은 `feat/v1.11.0-roadmap-ultra` branch 격리. main 미오염.
- 문제 시 branch 폐기 또는 특정 commit revert. v1.10.0 stable(`d15e6d6`)은 tag로 고정돼 영향 0.
- 모듈화가 entrypoint를 깨면 즉시 wrapper 복원 또는 branch reset.

## 진행 순서 제안

1. (이번) 로드맵·설계 문서·예제·readiness 점검 → merge 가능한 후보 상태
2. (다음 owner 승인 후) Lane B 모듈화를 단계적 wrapper로 실제 구현 + backward-compat 검증
3. interactive UX 최소 prototype
4. PyPI readiness 충족(pyproject 보강) 후 build 검증
5. 전체 게이트 통과 시 v1.11.0 stable 승격
