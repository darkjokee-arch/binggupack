# scripts/ Refactor Plan (Lane B)

> **상태: 설계만 — 실제 대규모 이동은 이번 사이클에서 BLOCKED.**
> 이유: `scripts/`에 116개 `.py`가 있고, `openbinggu_doctor.py`·각 `--selftest`·README/INSTALL의 명령이 현재 경로에 강하게 의존합니다. 일괄 이동은 backward-compat을 깰 위험이 높아, 승인 전 실제 이동을 수행하지 않습니다.

## 현재 구조 (사실)

- top-level: `binggu.py` (사용자 CLI 진입점)
- `scripts/`: 116 `.py` — 실행 스크립트 + selftest + live runner + one-off가 평면 혼재
- `hooks/`, `hosted/`, `docs/`, `tests/fixtures/`, `examples/`
- `binggupack/` 패키지 **없음** (평면 구조)
- 핵심: `scripts/smoke_test.py`, `scripts/install_claude_mcp.py`는 **독립 실행**(패키지 import 0) → 모듈화해도 entrypoint 보존 가능

## 목표 구조 (제안)

```text
binggupack/
  __init__.py
  cli/        # binggu.py 로직 이관
  core/       # ledger·candidate·save gate 핵심
  mcp/        # openbinggu_mcp_server 로직
  pack/       # pack_build·validate·publish_guard·consumer_smoke
  safety/     # G4·PII·confirm gate

scripts/
  smoke_test.py          # ← thin wrapper (binggupack.* 호출)
  install_claude_mcp.py  # ← thin wrapper
  oneoffs/               # 1회성 backfill·migration
  dev/                   # 개발·검증 selftest 러너
```

## backward-compat 불변식 (반드시 유지)

1. `python scripts/smoke_test.py` 계속 실행 가능
2. `python scripts/install_claude_mcp.py` 계속 실행 가능
3. `python binggu.py ...` 모든 서브커맨드 동일
4. `python scripts/openbinggu_mcp_server.py --serve <root>` 동일 (MCP 등록 경로 불변)
5. README / INSTALL의 명령 문자열 변경 0
6. 각 `--selftest` 게이트 동일 결과

## 단계적 마이그레이션 전략 (승인 후)

- **Phase 1** — `binggupack/` 패키지 생성, 핵심 로직을 모듈로 *복사*(원본 유지). import 동작 확인.
- **Phase 2** — 기존 스크립트를 thin wrapper로 전환(내부에서 `binggupack.*` 호출). 실행 결과 byte-동일 검증.
- **Phase 3** — one-off/dev 러너를 `scripts/oneoffs`, `scripts/dev`로 이동. 단 selftest 경로 참조 일괄 갱신 + 회귀 검증.
- **Phase 4** — README/INSTALL 명령은 불변 유지(이동된 건 내부 러너뿐).
- 각 Phase마다 smoke 10/10 + install dry-run + `binggu.py --selftest` 회귀.

## 위험 (why BLOCKED now)

- 116 스크립트의 상호 import / 상대경로 / `__file__` 기준 fixture 로드가 이동에 민감.
- `openbinggu_doctor.py`가 다수 selftest를 경로로 묶어 호출 → 이동 시 일괄 깨짐 가능.
- 일괄 git mv는 한 commit에 5+ 파일 동시 변경 → 검증 없는 대량 이동 = 사장님 §3-5 위반.

## 판정

**Lane B = BLOCKED (설계 문서로 격리).** 실제 이동은 owner 승인 + Phase별 회귀 검증을 전제로 다음 사이클에서 진행 권장.
