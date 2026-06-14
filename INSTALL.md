# INSTALL — BingguPack

> `scripts/`·`docs/`의 `openbinggu_`/`OPENBINGGU_` 접두사는 레거시 내부 코드네임입니다(BingguPack과 동일 프로젝트).

> **무엇인가** — local-first · evidence-backed context packs · candidate capture/preview · human-confirmed SAVE gate · ontology graph validation. **자동 ledger/confirmed write 없음** — 저장은 preview → `SAVE n`(정확한 confirm) 게이트로만 진행됩니다.
>
> **Latest release = v1.5.0.** `binggu init`이 현재 workspace scope에 자동 후보 수집을 기본 ON으로 만드는 capture profile(scope-gated) + 영속 candidate 버퍼 + opt-in hook support + `binggu hosted pull`(폰 SAVE n→PC 한 번에). **자동 저장은 없음**(저장 = preview → `SAVE n`). 라이브 save-intent는 **신형 v2 서명 전용**(`SAVE_SIG_V2_ONLY=1`, 구형 차단). 라인: v1.0.0(개인 장부) → v1.1.0(그래프 문법) → v1.2.0(hosted save-intent) → v1.3.x(자동 캡처·preview·어댑터) → v1.4.0(AGI memory capture)~v1.4.6(hosted pull·온보딩) → **v1.5.0: PC-mediated read publish pipeline P1~P8 구현(로컬) + evidence graph 문법/pack repair → v1.5.1(후보, 미발행): Windows/WSL/macOS cross-platform 지원(`BINGGU_HOME` opt-in 공유). 최신 발행 태그는 v1.5.0이며 OpenCrab Cloud ingest는 HOLD.**
>
> OpenCrab 업로드는 **planned**(preflight G1~G7까지 구현·검증, 실 전송은 별도 결정 — 노출 0). "100% 완성판"이 아니며 모든 사용자 환경 동작을 보장하지 않습니다. 전체 로드맵·범위는 `README.md`, 따라하기는 `docs/BINGGUPACK_TUTORIAL.md` 참조.

## Requirements / 요구사항
- Python 3.10+ (표준 라이브러리 위주)
- OS: Windows / WSL / macOS / Linux — 같은 정책으로 동작
- python 런처: **Windows `py`** · **WSL/macOS/Linux `python3`** (아래 예시의 `python`을 OS에 맞게 바꿔 쓰면 됩니다)
- 장부 위치: 기본은 OS별 로컬 홈(Windows `%USERPROFILE%\.binggupack`, WSL/macOS `~/.binggupack`). OS 간 같은 장부 공유는 `BINGGU_HOME` 명시(opt-in) — [docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md](docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md)

## Install / 설치

macOS / WSL / Linux (bash):
```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python3 -m venv .venv
source .venv/bin/activate   # 선택
python3 scripts/binggu_platform_selftest.py   # cross-platform 경로·lock 정책 36/36 GATE=GO
```

Windows (PowerShell):
```powershell
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
py -m venv .venv
.\.venv\Scripts\Activate.ps1   # 선택
py scripts\binggu_platform_selftest.py   # cross-platform 경로·lock 정책 36/36 GATE=GO
```

## Verify / 동작 확인 (권장 진입점)
```bash
python scripts/openbinggu_doctor.py --selftest          # 15/15 PASS GATE=GO 기대
python scripts/openbinggu_doctor.py --tree examples/toy_project   # CLEAN 기대
```

## AGI memory capture (opt-in) / 자동 후보 수집
```bash
python binggu.py init --agi-memory    # 장부 + capture profile (전역 후보수집 = AGI memory mode, 기본 ON)
python binggu.py init                 # 현재 위치만(privacy 모드)
python binggu.py init --global        # --agi-memory 와 동일(작업 전역)
python binggu.py init --no-capture    # 장부만(capture 생략)
python binggu.py capture status       # ON/OFF · scope · 버퍼 건수 · hook 등록 여부
python binggu.py capture pause        # 일시중지
python binggu.py capture resume       # 재개
python binggu.py capture preview      # 수집 후보 목록 + 저장 명령 안내 (저장 0)
python binggu.py capture uninstall    # 완전 제거(rollback) — 장부는 보존
```
검증:
```bash
python scripts/binggu_capture_persist.py         # 16/16 (영속 버퍼·scope·TTL·pause·global)
python scripts/binggu_capture_profile.py         # 9/9  (profile·settings hook·pause/resume/uninstall)
python hooks/binggu_capture_hook.py --selftest   # 8/8  (UserPromptSubmit/Stop 진입점)
python binggu.py --selftest                      # 26/26 (장부 + capture + hosted 통합)
```
> **자동 저장이 아니라 자동 후보 수집입니다.** `binggu init`이 만든 profile 안에서만 동작 — clone 직후엔 수집 0. **AGI memory(`--agi-memory`/`--global`)는 작업 전역**이 기본 경험, privacy(`init`)는 현재 위치만. 어느 scope든 시크릿/PII 발화는 자동 후보 제외 + 시크릿 디렉토리 deny. ledger/active/confirmed write 0. 저장은 preview → `SAVE n` 게이트만. scope·hook·롤백 상세: `docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md`.

## Reviewer/confirmed preview selftest / 리뷰·확정 preview 검증 (preview only)
```bash
python scripts/openbinggu_phase4_reviewer_confirmed_selftest.py   # 9/9 PASS GATE=GO 기대
python scripts/openbinggu_reviewer_auth_session_selftest.py       # 20/20 PASS GATE=GO 기대
```
> **PREVIEW ONLY** — confirmed_created=0 / applied=0 / promoted=0 / upload=0 을 selftest가 강제합니다. confirmed 생성·적용은 이 RC에 포함되지 않습니다.

## Local persistence selftest / 로컬 저장 검증 (opt-in 기능 검증)
```bash
python scripts/openbinggu_phase2_local_persistence_selftest.py   # 11/11 PASS GATE=GO 기대
python scripts/openbinggu_phase2_staging_reread_e2e.py           # 10/10 PASS GATE=GO 기대(read-only 재독)
python scripts/openbinggu_batch_pack_loader.py --selftest        # 10/10 PASS GATE=GO 기대(batch pack→staging apply→rollback)
python scripts/openbinggu_promotion_preview.py --selftest        # 12/12 PASS GATE=GO 기대(read-only promotion preview)
```
> 위 selftest는 **temp OPENBINGGU_HOME** 기준입니다(실제 사용자 홈에 write 0). 실제 저장 기능은 **write 기본 OFF**·명시 opt-in·CLI 전용이며, MCP write 도구는 노출되지 않습니다. candidate-only(`promotion_allowed=0`), confirmed/promote/OpenCrab/Neo4j는 HOLD.

## Manual capture selftest / 수동 캡처 검증 (read-only)
```bash
python scripts/openbinggu_phase6_manual_capture_selftest.py   # 10/10 PASS GATE=GO 기대
```
> **synthetic / temp / read-only** 기준. 사용자가 명시 지정한 경로만 capture(allowlist only, denylist 우선), raw 저장 0·source pointer 공개 미포함. **write opt-in 없으면 staging write 0.** 대화 발화 기반 자동 후보 수집은 별도 opt-in hook입니다(위 §자동 후보 수집 hook 참조 — 기본 OFF, candidate-only).

## Finalize dry-run selftest / finalize 조립 검증 (로컬 생성만)
```bash
python scripts/openbinggu_finalize_dryrun.py --selftest   # 10/10 PASS GATE=GO 기대
```
> pack v1 레이아웃을 **로컬에 조립만** 합니다 — upload/apply 0, **Neo4j 실행 0**(import.cypher는 파일 생성만, export_status=NOT_RUN). license는 `{scope:"personal", name:"MIT"}`, release_mode/entitlement는 비필드.

## Personal write loop selftest / 개인용 쓰기 루프 검증 (temp-only)
```bash
python scripts/openbinggu_v08_real_cycle_once.py --dry-run-temp          # 14/14 PASS GATE=GO 기대 (preview→선택 저장→피드백 통합, temp만)
python scripts/openbinggu_v08_review_resolve_4values.py --selftest       # 16/16 PASS GATE=GO 기대 (4값 resolve: 성공/실패/불확실/판정불가)
```
> 저장은 **로컬 CLI 전용·opt-in·candidate-only**(`promotion_allowed=0`)이며 원문(대화 전문)은 저장되지 않습니다(선택 문장 발췌만). resolve는 **기록만** — `실패`여도 자동 강등 0. hosted(채팅)에서의 저장(save-intent)은 **v1.2.0부터 동작 검증됨** — `README.md` 상태표·`docs/BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md` 참조 (이 selftest는 로컬 CLI 경로만 검증).

## Candidate management UX selftest / 후보 관리 UX 검증 (temp-only)
```bash
python scripts/openbinggu_candidate_list_view.py --selftest              # 13/13 PASS GATE=GO 기대 (read-only 목록)
python scripts/openbinggu_candidate_deprecate_ux.py --selftest           # 15/15 PASS GATE=GO 기대 (기각)
python scripts/openbinggu_candidate_replace_ux.py --selftest             # 16/16 PASS GATE=GO 기대 (수정 transaction)
python scripts/openbinggu_owner_accept_ux.py --selftest                  # 16/16 PASS GATE=GO 기대 (수용·철회)
python scripts/openbinggu_v1_candidate_cycle_real_once.py --dry-run-temp # 17/17 PASS GATE=GO 기대 (보기→기각→수정→수용→철회→resolve 통합 사이클)
```

### 후보 관리 사용법 (confirm 문구 형식)

모든 변경 작업은 **human confirm 문구 정확 일치** 의무입니다 — 목록 보기에서 행 번호 `<n>` 과 id 칼럼의 `<id8>`(node hash 8자)을 함께 적습니다(인덱스 단독 금지).

1. **목록 보기** (read-only — checksum 불변):
   `candidate_list` 뷰로 행 번호·id8·상태(pending/resolved/deprecated)·kind를 확인합니다.
2. **기각** — confirm 문구: `DEPRECATE <n> <id8>` (예: `DEPRECATE 3 a1b2c3d4`)
   물리 삭제가 아니라 보존형 제외(active 뷰에서만 빠짐).
3. **수정** — confirm 문구: `REPLACE <n> <id8> WITH <수정문장>`
   in-place 수정이 아니라 transaction: 전임자는 `replaced_by:` back-link와 함께 deprecate, 신규 문장은 저장 게이트(헌법 재판정·PII 재스캔·중복 검사) 전부 재통과한 새 candidate.
4. **수용/철회** — confirm 문구: `ACCEPT <n> <id8>` / `UNACCEPT <n> <id8>`
   append-only event 기록만(후보 row 자체는 byte-identical 보존). deprecated 후보 ACCEPT는 BLOCK, 중복 ACCEPT도 BLOCK.
5. **피드백 resolve** — 판단 노드의 검증예정일 도래 시 4값(`성공/실패/불확실/판정불가`) + 사유 필수. 기록만이며 노드 상태는 무변.

> `actor=auto`는 전부 BLOCK — 사람 발화 유래 confirm만 허용. 통합 흐름 실연: `docs/OPENBINGGU_V1_CANDIDATE_CYCLE_RESULT.md`.

## PC-mediated read publish pipeline selftest / 퍼블리시 파이프라인 검증 (P1~P8)
```bash
py scripts/binggu_publish_run_all_selftests.py    # 8/8 PASS · REGRESSION=GO (P1~P6 + cloud_pack export + tree scan)
```
> 회귀 검증만 수행합니다 — **Cloud upload / DB insert / OpenCrab ingest 0**. P3는 실 ledger를 read-only(mode=ro)로만 읽고, active 데이터 없으면 `NO_REAL_LEDGER_DATA`로 BLOCK합니다.

## Cross-platform selftest / 크로스플랫폼 정책 검증 (Windows/WSL/macOS)
```bash
py scripts\binggu_platform_selftest.py            # 36/36 GATE=GO (Windows)
python3 scripts/binggu_platform_selftest.py       # 36/36 GATE=GO (WSL/macOS/Linux)
```
> OS별 홈·`BINGGU_HOME` opt-in 공유·경로 변환(표시용)·lock 충돌 fail-closed를 검증합니다. WSL/macOS 경로 규칙은 synthetic(입력 주입)으로, lock 충돌은 temp 장부 실측으로 확인합니다. 자세히: [docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md](docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md).

## MCP / MCP 연결 (선택)
`mcp.example.json` 참고. read/dry-run 도구만 노출됩니다(write/apply/push 미노출).

> 공개/업로드 전에는 `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>` 가 CLEAN 이어야 하며, owner 수동 승인 후에만 push/upload 하세요. 자세한 절차는 `README.md`·`docs/` 참고.
