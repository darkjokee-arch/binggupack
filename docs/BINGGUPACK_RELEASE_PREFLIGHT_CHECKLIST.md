> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# BingguPack 1차 배포 — 배포 전 GO/BLOCK 체크리스트 (E)

> **상태: preflight 체크리스트(2026-06-08). docs only · 실 push 0.**
> 상위: [FIRST_RELEASE_GITHUB_MCP_DESIGN](BINGGUPACK_FIRST_RELEASE_GITHUB_MCP_DESIGN.md) · [REPO_LAYOUT](BINGGUPACK_RELEASE_REPO_LAYOUT.md) · [MCP_EXPOSURE](BINGGUPACK_MCP_EXPOSURE_CANDIDATE.md).

---

## 1. GO 조건 (전부 충족해야 1차 배포 후보 GO)

| # | 항목 | 확인 방법 | 현재 |
|---|---|---|---|
| P1 | selftest 4개 GATE=GO / EXIT=0 | scope_envelope·builder·validate·consumer_smoke 실행 | ✅ 실측 |
| P2 | source pointer dirty/unknown → 공개 BLOCK | selftest dirty=RESIDUAL_DIRTY / unknown=MASK_UNKNOWN | ✅ |
| P3 | secret/PII scan 기준 충족 | 공개 트리 rglob + 정규식 → 0건(존재·길이만 보고) | ⏳ push 직전 실행 |
| P4 | repo 공개 제외 경로 확인 | `.gitignore` 매칭 + `git check-ignore` | ⏳ repo 구성 후 |
| P5 | raw 경로/secret 미출력 | 모든 도구 출력 count/reason_code/source_pointer_id 만 | ✅ |
| P6 | README/INSTALL/MCP config 예시 존재 | 문서 확인 | ✅ 초안 |
| P7 | MCP 노출 = read/dry-run only | write/apply/push 도구 미등록 | ✅ 설계 |
| P8 | owner 수동 승인 단계 명시 | 게이트2 approve 후에만 push | ✅ 설계 |

## 2. BLOCK 조건 (하나라도 해당 시 배포 금지)

- ❌ selftest 1개라도 GATE≠GO 또는 EXIT≠0.
- ❌ 공개 트리에 실 그래프/DB/sqlite/reports/reviews/captures/evidence 원본 존재.
- ❌ `.env`/credential/token/key 추적됨.
- ❌ source pointer dirty/unknown인데 publish_allowed=true(fail-open).
- ❌ MCP가 OpenCrab write/apply/ingest/push/sanitizer 자동치환/enum 확정/team_paid 기능 노출.
- ❌ 도구가 raw PII/secret/private path 출력.
- ❌ owner 승인 없이 push 자동화.

## 3. 배포 직전 마지막 게이트 (공개 시점 — 두 경로 공통)

> "공개"는 두 경로 모두 동일 게이트: ① GitHub 공개 push, ② **사용자 자기 OpenCrab 계정 업로드**(가입자 자발). OpenCrab은 우리가 쌓는 중앙 store가 아니라 사용자가 올리는 곳이며, 업로드도 GitHub 공개와 같은 fail-closed gate를 거친다.

1. P3·P4 실제 실행(공개 대상 트리/pack secret/PII scan + check-ignore).
2. dirty/unknown source pointer·raw PII/secret/private path → 두 경로 모두 BLOCK.
3. owner/user가 공개 요약(무엇을·어디로·항목 수) 확인 후 **1회 명시 승인**.
4. 승인 후에만 push/업로드. 자동/일괄 금지. 이전 승인 재사용 금지.

> ❗ 별개 HOLD: **우리 시스템/운영자가 OpenCrab store에 자동 write/apply/ingest** 하는 경로는 본 게이트와 무관하게 계속 HOLD(사용자 자발 업로드와 구분).

## 4. 현재 판정

- P1·P2·P5·P6·P7·P8 = 현 시점 충족(설계/실측). P3·P4 = repo 구성·push 직전 실행 대상.
- **선결 gate docs 기준 정리 완료(2026-06-08)**: S1 clean repo 절차([CLEAN_REPO_BOOTSTRAP](BINGGUPACK_CLEAN_REPO_BOOTSTRAP.md)) · S2 source pointer 미포함 디폴트([SANITIZER_POLICY_BLOCK_ONLY](BINGGUPACK_SANITIZER_POLICY_BLOCK_ONLY.md) §4-5) · X2 toy E2E([TOY_E2E_USER_SCENARIO](BINGGUPACK_TOY_E2E_USER_SCENARIO.md)) · S4 실데이터 검증 절차([REAL_DATA_VALIDATION_PROCEDURE](_archive/BINGGUPACK_REAL_DATA_VALIDATION_PROCEDURE.md)) · 사용자 주도 업로드 흐름([USER_DRIVEN_OPENCRAB_UPLOAD_FLOW](BINGGUPACK_USER_DRIVEN_OPENCRAB_UPLOAD_FLOW.md) — **docs 기준만, 실 업로드 기능 미구현**).
- **S3·X1 path safety gate(2026-06-08)**: `scripts/openbinggu_path_safety_gate.py` — allow_root 격리 + 탈출(symlink/junction/UNC/ADS/8.3/parent) 차단 + denylist(bid-engine·NPKI/인증서·secret·OpenCrab store·타프로젝트), ALLOW/BLOCK+reason_code(raw 미출력). **selftest 15/15 GATE=GO/EXIT=0**.
- **S3·X1 MCP 실연결 adapter(2026-06-08)**: `scripts/openbinggu_mcp_path_gate_adapter.py` — `guarded_tool_call`로 도구 실행 직전 재검사(TOCTOU 잔여 감소), BLOCK 시 underlying 미호출, raw 미출력. **selftest 10/10 GATE=GO/EXIT=0**(ALLOW만 underlying 실행). 남은 것 = 실제 MCP 서버 등록/공개(별도 GO).
- **S5 openbinggu doctor 단일 진입점(2026-06-08)**: `scripts/openbinggu_doctor.py --selftest` — 6개 selftest(scope_envelope·builder·validate·consumer_smoke·path_safety·mcp_adapter)를 subprocess **호출만**(신규 로직 재구현 0=공격면 최소) + secret/PII scan dry-run stub + operating store 불변 확인. 요약(PASS/FAIL·reason_code·count)만, raw 미출력. **8/8 PASS GATE=GO/EXIT=0**.
- **S4 실데이터 검증(2026-06-08)**: 절차 docs 정의 완료([REAL_DATA_VALIDATION_PROCEDURE](_archive/BINGGUPACK_REAL_DATA_VALIDATION_PROCEDURE.md)) + **실 트리 secret/PII 스캐너 결선 완료** — `scripts/openbinggu_public_tree_scan.py`(read-only, ignore_globs `.gitignore` 연동, count/reason_code/file_id만·raw 미출력) + `doctor --tree <ROOT>`(검출 1건↑ → verdict=BLOCK·GATE=NO-GO·exit 1). scanner selftest 3/3 GATE=GO, doctor 9/9 GATE=GO.
- **MCP 핸들러 결선 후보(2026-06-08)**: `scripts/openbinggu_mcp_server_handlers.py` — adapter `guarded_tool_call`을 read/dry-run 도구 핸들러(pack_build·pack_validate·consumer_smoke·publish_guard_dryrun·selftest)에 결선, path 입력 전부 gate 통과·BLOCK 시 미호출, 위험 도구(write/apply/push/upload/sanitizer/enum/team_billing/marketplace/db) 핸들러 부재→tool_not_exposed. **selftest 10/10 GATE=GO**, doctor 9/9 유지.
- **MCP stdio JSON-RPC wrapper(2026-06-08)**: `scripts/openbinggu_mcp_server.py` — initialize/tools·list(read/dry-run only)/tools·call(handle_tool 라우팅), malformed 안전 처리, 응답 sanitize(raw 미출력). **selftest 13/13 GATE=GO**. `--serve`는 정의만, 실 등록/공개 미실행.
- **clean repo 부트스트랩 계획(2026-06-08)**: 포함/제외 목록·doctor --tree 절차·승인 체크리스트·push 명령(실행 금지) 확정 → `BINGGUPACK_RELEASE_BOOTSTRAP_PLAN.md`(internal design doc — not included in public repo).
- **의존 audit 결과(2026-06-08)**: `BINGGUPACK_RELEASE_DEPENDENCY_AUDIT.md`(internal design doc — not included in public repo) — import closure 21모듈. 1차 위험키워드는 대부분 false positive(denylist·가드·박제·temp/report write). 핵심 운영 모듈(`watcher_op_m0`·`watcher_pack_builder_m0`) 코드 read = **operating store write 0 확정**(store는 불변체크 읽기만). **"read/dry-run only 공개" 실질 성립, write 가드 코드 수정 불필요**.
- **그룹 C 정밀 audit 완료(2026-06-08)**: `BINGGUPACK_RELEASE_GROUP_C_AUDIT.md`(internal design doc — not included in public repo) — 8개 모듈 ast+코드 read = **operating store/DB/apply write 0 확정**(import-time side effect=docstring 오탐, write=report/temp, run_selftest 'store!'=불변체크+report 공존 오탐). consumer_smoke→incoming_to_staging는 `SECRET_PATTERNS` 상수만 참조. **import closure 21모듈 전체 store write 0 → "read/dry-run only 공개" 완전 성립, blocker 없음.**
- **examples/toy_project 생성 완료(2026-06-08)**: `examples/toy_project/{README.md,input/toy_notes.md,expected/toy_pack_summary.json}` synthetic. **`doctor --tree examples/toy_project` → real_tree_scan CLEAN(hits 0), 9/9 GATE=GO**. source pointer 미포함 디폴트·dirty/unknown BLOCK 설명·OpenCrab 업로드 docs 기준(실 API 미구현) 포함.
- **남은 작업(별도 GO, 차단 아님)**: ① (선택) SECRET_PATTERNS 상수 분리·트리 최소화 import 정리 ② 실 MCP 설정 등록/공개 ③ 실 OpenCrab 업로드 기능/API/MCP 연결 ④ 실 GitHub repo push(owner 승인).
- **1차 배포 = 방향 GO 후보**(설계·RC 기준). 실제 push/업로드는 owner/user 승인 + P3·P4 실행 + 코드 gate(S3·X1) 후.

## 5. 안전

docs only. push·production·OpenCrab/store/DB·apply/ingest/merge·enum·team_paid·marketplace·sanitizer·raw 출력 0. operating store mtime 불변.
