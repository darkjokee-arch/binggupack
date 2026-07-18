# BingguPack — consent-first 개인 AI 기억 팩

> "AI가 기억해도, 결정권은 나에게." local-first·evidence-backed·stdlib-only(의존성 0) 기억/컨텍스트 팩 + Claude Code MCP 서버(stdio).
> 전역 규칙(`~/.claude/CLAUDE.md`) §8-1(자기진화 거버넌스)·§12(supersede) 우선 적용.

## 🔴 절대 경계 (위반 = 작업 중단)
1. **운영홈은 repo 밖:** `~/.binggupack/` (`BINGGU_HOME` env 우선), 본체 = `~/.binggupack/ledger.sqlite`. **모든 테스트·preflight는 임시 BINGGU_HOME 격리** — 작업 후 운영홈 ledger 본체 mtime 불변 실측이 관례. repo 안 `_binggu_*_home/`·`_fc_home/`은 샌드박스(운영 아님).
2. **저장은 사장님 SAVE만:** owner 발화 저장은 preview 제시 → 사장님이 직접 SAVE/confirm. AI가 `binggu pair/save --confirm`을 Bash로 대행 = 승인 위조 (박제 `feedback_binggupack_never_ai_execute_save`). owner 발화는 `pair`로만(평면 save BLOCK)·원문 그대로. 상세 정본: 전역 §8-1 ⑥.
3. **PyPI 승인 대행 금지:** publish.yml `environment: pypi` 승인은 **owner 직접** — AI 대신 승인 0.
4. **거버넌스 자산 write 0:** 빙구팩이 박제/CLAUDE.md/정책파일 직접 수정 금지 (전역 §8-1). `policies/binggu_policy.json`은 sha256 무결성 쌍으로 관리.

## 스택·정체성
- Python ≥3.10, **런타임 의존성 0(stdlib-only)** — 의존성 추가 = 설계 위반, 착수 전 사장님 상의
- **버전 SSOT = `binggupack/__about__.py`** (pyproject와 triple-match를 CI·publish가 강제)
- Cloudflare Workers(TS+Python)는 `hosted/` 전용 — 로컬 코어와 분리 (`docs/BINGGUPACK_HOSTED_BOUNDARY.md`)

## 레이아웃
```
binggu.py            # 메인 CLI (binggu/binggupack 콘솔 진입점) — save·recall·pair·studio·preflight·index 등
binggupack/          # pip 패키지: pack(빌드·검증) safety(경로·시크릿 게이트) mcp studio(read-only 웹UI)
                     #   app(v1.21 read core) review capture classifier policy schema storage workspace cli
scripts/             # ~166개: *_selftest.py 게이트·publish 파이프라인·doctor·autopush·경로 SSOT(binggu_paths.py)
hosted/workers/      # Cloudflare Workers (wrangler.*.toml 다수) + anywhere/core = 벤더 사본(아래 함정 ①)
hooks/               # Claude Code 훅 (capture·preflight·save_gate·session_close·enforce-recall)
tests/               # pytest + 하니스(개수 가변 · 정본 = ls tests/) | docs/ 설계 정본(START_HERE·ARCHITECTURE·CONSTITUTION 2026-06-17)
```

## 검증 게이트 (커밋 전 이 순서로 — 목표: preflight 1회 통과)

> ⚠ **Windows 이 박스에서 pytest/preflight를 백그라운드로 띄우지 말 것** — python 서브프로세스 트리가 세션 kill로 exit 255 즉사(출력 0·2026-07-13 실측, 순수 sleep은 생존). 동기 실행 + 테스트 파일 2~3청크 분할(청크당 ≤5분). 정본: 전역 박제 `feedback_windows_background_kill_safety`.
```bash
python scripts/ci_local_preflight.py        # CI 전 게이트 로컬 재생(임시 홈 격리·운영홈 미터치) — 정본
python -m pytest                            # tests/ (scripts·hosted 제외 설정됨)
python binggu.py --selftest                 # 코어 selftest GATE=GO
python scripts/openbinggu_public_tree_scan.py --tree . --public   # verdict=CLEAN 필수
ruff check binggupack/ scripts/ --select F  # CI와 동일 범위
python scripts/sync_anywhere_vendor.py --check   # 공유 모듈 수정 시 필수(함정 ①)
```

## ⚠️ 반복 확인된 함정 (박제 승격 — 어길 시 CI red/루프)
1. **벤더 drift:** `binggupack/` 공유 모듈(daily·read core 등) 수정 시 `hosted/workers/anywhere/core/`에 byte-identical 벤더 사본 존재 — **머지 전 `sync_anywhere_vendor.py --check`** 안 하면 merged-main CI red (PR CI는 green이어도).
2. **시크릿 리터럴 자가오탐:** 소스/테스트에 `token|secret|key|password = "리터럴"` 금지 — tree scan이 BLOCK. 토큰은 `token_urlsafe()` 등 함수 생성, PII 테스트 값은 조각 조립(`"900101"+"-"+...`). 정본: `feedback_no_verify_fix_reverify_loop`.
3. **로컬 반복 통과 ≠ CI 결정성:** wall-clock 의존은 코드 읽기로 규명 (로컬 20회 GO여도 CI 전 OS fail 사례).
4. **WIP 혼입:** 병렬 세션·이전 브랜치 잔여물이 워킹트리에 섞임 — 의심되면 **git worktree 격리** 후 대상 파일만 재적용 (`BingguPack_wt_*` 형제 폴더가 그 용도).

## Git·릴리스 컨베이어
- 브랜치 → PR → **CI 전 매트릭스 green**(ubuntu 3.10/3.12/3.13/3.14·macos·windows + typecheck + MCP install) → `gh pr merge N --merge`
- **merge commit만** — squash/rebase/force-push/history rewrite/기존 tag 이동 **0**. 충돌 시 origin/main 보존 머지
- 릴리스: release 브랜치(version-only) → PR → merge → annotated tag(tag==HEAD 확인) → `gh workflow run publish.yml --ref v<X>`(workflow_dispatch·OIDC Trusted Publishing·토큰 0) → **owner pypi env 승인** → 공식 PyPI 설치본 smoke(저장소 밖 cwd·temp home) → GitHub release `--verify-tag`. 절차 상세: `docs/PYPI_RELEASE_VERIFICATION.md`
- Worker 배포(`deploy-worker.yml`·wrangler)는 사장님 지시 시에만

## 완료의 정의 (이 프로젝트)
1. 위 게이트 전부 직접 실행 GO (특히 preflight·tree scan CLEAN)
2. 운영홈 `~/.binggupack/ledger.sqlite` 본체 불변 실측
3. 공유 모듈 수정 시 벤더 `--check` 통과
4. PR CI 전 매트릭스 green (flaky는 무관 3중 확증 후 해당 job만 재실행 — 억지 green 위장 0)
5. 전역 §10 DoD (자가평가·traj·memory sync 포함)
