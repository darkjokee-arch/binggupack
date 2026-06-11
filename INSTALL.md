# INSTALL — BingguPack (Personal Track)

> OpenBinggu is the legacy/internal codename for BingguPack.

> **Track1 public RC** — v0.1.0-rc1: read/dry-run + pack validation + MCP 5도구 / v0.2.0-rc1: +local persistence(candidate-only, opt-in, write 기본 OFF) / v0.3.0-rc1: +manual one-shot capture(read-only) / v0.3.1-rc1: +batch pack staging loader / v0.4.0-rc1: +promotion preview(read-only) / v0.5.0-rc1: +reviewer/confirmed preview(read-only) / v0.6.0-rc1: +finalize dry-run generator / v0.6.1-rc1: 문서·온보딩 fix + hosted MCP skeleton 로컬 PoC / v0.7.0-rc1: hosted connector 실구현(`docs/BINGGUPACK_HOSTED_CONNECTOR_PHASE1_RESULT.md`) / v0.7.1-rc1: 실 pack(마스킹) hosted 탑재 + Claude·ChatGPT 양사 커넥터 검증 / v0.7.2-rc1: +conversation_capture_preview(hosted 6번째 read-only 도구, 저장 0) / v0.8.0·v0.8.1-rc1: +개인용 쓰기 루프(로컬 CLI 저장, candidate-only) / **v0.9.0-rc1(최신)**: +후보 관리 UX 완성(candidate_list·DEPRECATE·REPLACE·ACCEPT/UNACCEPT·4값 resolve). hosted write(save-intent)·OpenCrab upload는 **planned (design complete, separate GO)** — 구현·노출 0. "100% 완성판"이 아니며, 모든 사용자 환경 동작을 보장하지 않습니다. 전체 로드맵·범위는 `README.md`, 따라하기는 `docs/BINGGUPACK_TUTORIAL.md` 참조.

## Requirements / 요구사항
- Python 3.10+ (표준 라이브러리 위주)
- OS: Windows / macOS / Linux

## Install / 설치

macOS/Linux (bash):
```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python -m venv .venv
source .venv/bin/activate   # 선택
```

Windows (PowerShell):
```powershell
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # 선택
```

## Verify / 동작 확인 (권장 진입점)
```bash
python scripts/openbinggu_doctor.py --selftest          # 12/12 PASS GATE=GO 기대
python scripts/openbinggu_doctor.py --tree examples/toy_project   # CLEAN 기대
```

## Reviewer/confirmed preview selftest / 리뷰·확정 preview 검증 (v0.5.0-rc1, preview only)
```bash
python scripts/openbinggu_phase4_reviewer_confirmed_selftest.py   # 9/9 PASS GATE=GO 기대
python scripts/openbinggu_reviewer_auth_session_selftest.py       # 20/20 PASS GATE=GO 기대
```
> **PREVIEW ONLY** — confirmed_created=0 / applied=0 / promoted=0 / upload=0 을 selftest가 강제합니다. confirmed 생성·적용은 이 RC에 포함되지 않습니다.

## Local persistence selftest / 로컬 저장 검증 (v0.2.0-rc1, opt-in 기능 검증)
```bash
python scripts/openbinggu_phase2_local_persistence_selftest.py   # 11/11 PASS GATE=GO 기대
python scripts/openbinggu_phase2_staging_reread_e2e.py           # 10/10 PASS GATE=GO 기대(read-only 재독)
python scripts/openbinggu_batch_pack_loader.py --selftest        # 10/10 PASS GATE=GO 기대(batch pack→staging apply→rollback)
python scripts/openbinggu_promotion_preview.py --selftest        # 12/12 PASS GATE=GO 기대(v0.4.0-rc1 read-only promotion preview)
```
> 위 selftest는 **temp OPENBINGGU_HOME** 기준입니다(실제 사용자 홈에 write 0). 실제 저장 기능은 **write 기본 OFF**·명시 opt-in·CLI 전용이며, MCP write 도구는 노출되지 않습니다. candidate-only(`promotion_allowed=0`), confirmed/promote/OpenCrab/Neo4j는 HOLD.

## Manual capture selftest / 수동 캡처 검증 (v0.3.0-rc1, read-only)
```bash
python scripts/openbinggu_phase6_manual_capture_selftest.py   # 10/10 PASS GATE=GO 기대
```
> **synthetic / temp / read-only** 기준. 사용자가 명시 지정한 경로만 capture(allowlist only, denylist 우선), raw 저장 0·source pointer 공개 미포함. **write opt-in 없으면 staging write 0**, **hook/daemon은 NOT_STARTED**(설치/실행 0). (reviewer/confirmed preview selftest는 v0.3.0 당시 미포함 — **v0.5.0-rc1에서 preview로 추가됨**, 위 §Reviewer/confirmed 참조.)

## Finalize dry-run selftest / finalize 조립 검증 (v0.6.0-rc1, 로컬 생성만)
```bash
python scripts/openbinggu_finalize_dryrun.py --selftest   # 10/10 PASS GATE=GO 기대
```
> pack v1 레이아웃을 **로컬에 조립만** 합니다 — upload/apply 0, **Neo4j 실행 0**(import.cypher는 파일 생성만, export_status=NOT_RUN). license는 `{scope:"personal", name:"MIT"}`, release_mode/entitlement는 비필드.

## Personal write loop selftest / 개인용 쓰기 루프 검증 (v0.8, temp-only)
```bash
python scripts/openbinggu_v08_real_cycle_once.py --dry-run-temp          # 14/14 PASS GATE=GO 기대 (preview→선택 저장→피드백 통합, temp만)
python scripts/openbinggu_v08_review_resolve_4values.py --selftest       # 16/16 PASS GATE=GO 기대 (4값 resolve: 성공/실패/불확실/판정불가)
```
> 저장은 **로컬 CLI 전용·opt-in·candidate-only**(`promotion_allowed=0`)이며 원문(대화 전문)은 저장되지 않습니다(선택 문장 발췌만). resolve는 **기록만** — `실패`여도 자동 강등 0. hosted(채팅)에서의 저장(save-intent)은 **planned (design complete, separate GO)** 로 이 RC에 노출되지 않습니다.

## Candidate management UX selftest / 후보 관리 UX 검증 (v0.9.0-rc1, temp-only)
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

## MCP / MCP 연결 (선택)
`mcp.example.json` 참고. read/dry-run 도구만 노출됩니다(write/apply/push 미노출).

> 공개/업로드 전에는 `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>` 가 CLEAN 이어야 하며, owner 수동 승인 후에만 push/upload 하세요. 자세한 절차는 `README.md`·`docs/` 참고.
