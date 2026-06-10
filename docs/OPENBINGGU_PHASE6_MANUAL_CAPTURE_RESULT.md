> OpenBinggu is the legacy/internal codename for BingguPack.

# BingguPack Phase 6 — Manual One-Shot Capture 결과 (2026-06-09)

> 설계: `OPENBINGGU_PHASE6_AUTO_CAPTURE_PLAN.md`. 구현: `scripts/openbinggu_phase6_manual_capture_selftest.py`.
> ⚠️ 전부 **synthetic / temp / read-only selftest**. hook 설치·daemon 실행·실 홈 write·운영 store write·OpenCrab·Neo4j·confirmed **0**. (작성 당시 상태: 코드 작업트리만·RC 미반영·push 0.)
> 📌 **현재**: 이 기능은 **v0.3.0-rc1**로 공개 RC에 반영되어 있습니다. 본문의 "작업트리만/push 0" 표현은 작성 시점 기록입니다.
> 표현 규칙: "자동 적용/완료/보장"·"production 보장"·"실유출 0" 금지 → "synthetic 기준 GATE=GO", "현재 fixture/temp 기준 추가 노출 미검출".

## 1. 목적
사용자가 **명시 지정한 파일/폴더만 read-only**로 읽어 candidate capture 후보를 만드는 manual one-shot 흐름 검증. capture는 **후보(id/hash)까지만** — staging write는 opt-in, 자동 write 0.

## 2. 핵심 결과
- **RESULT: 10/10 PASS · GATE=GO** (synthetic/temp)
- operating_store_unchanged=True · **raw_leak=0** · **write=0** · **hook_started=0** · **daemon_started=0**
- opencrab=0 · neo4j=0 · confirmed=0 · 실 홈 write 0(temp only)
- doctor 회귀 11/11 GATE=GO (phase6 신규 파일, doctor `_CHECKS` 무관)

## 3. Q1~Q10 케이스
| # | 항목 | 기대/결과 |
|---|---|---|
| Q1 | allowlist file read OK | ALLOW captured=1, staged=False(opt-in OFF) ✅ |
| Q2 | denylist `.env` BLOCK | BLOCK `denylist` ✅ |
| Q3 | credentials/private key BLOCK | BLOCK `denylist`(id_rsa) ✅ |
| Q4 | bid-engine/NPKI/browser/sqlite BLOCK | BLOCK `denylist`(sqlite·bid-engine) ✅ |
| Q5 | raw_leak=0 | 경로/원문/secret needle 0(반환/audit/candidate 대상) ✅ |
| Q6 | rate limit 초과 BLOCK | BLOCK `rate_limit` ✅ |
| Q7 | kill switch ON → BLOCK | BLOCK `kill_switch` ✅ |
| Q8 | fail-closed unknown source | BLOCK `fail_closed_unknown`(allowlist 밖) ✅ |
| Q9 | write opt-in 없음 → staging write 0 | staging_writes=0 ✅ |
| Q10 | hook/daemon NOT_STARTED | hook_started=0·daemon_started=0 ✅ |

## 4. Q1/Q5 1차 실패 원인 + 정정
- **Q1 FAIL**: `_norm`이 `os.path.normcase(p.replace("\\","/"))` — Windows `normcase`가 `/`를 다시 `\`로 변환해, allowlist startswith 매칭이 `\`(경로)와 `/`(붙인 구분자) 불일치 → allow 경로가 `unknown(fail_closed)`로 오분류.
  - **정정**: `_norm = normcase(abspath)`, allowlist 매칭을 `n.startswith(root + os.sep)`로(OS 표준 분리자). deny 패턴 검색은 `/` 기준 사본(`nslash`)으로 분리.
- **Q5 FAIL**: raw_leak 검사 blob에 `results`(테스트 케이스 **설명** 문자열, 예: "denylist .env BLOCK"·"bid-engine/sqlite 경로 BLOCK")를 포함해 needle ".env"·"bid-engine"과 **false-positive** 매칭. 실제 raw 경로 노출 아님.
  - **정정**: leak 검사 대상을 **실제 데이터(capture 반환·audit·candidate)** 로 한정, `results`(설명) 제외.

## 5. 교훈
- **Windows 경로 매칭은 `normcase(abspath)` + `os.sep` 기준**으로 통일. `.replace("\\","/")` 후 `normcase`는 `/`를 `\`로 되돌려 매칭이 깨진다(Phase 2·4에 이어 경로/케이스 1차 FAIL 3회째 — 박제).
- **leak/needle 검사는 실데이터(반환/audit/candidate)만 대상**. 테스트 케이스 설명(results)을 포함하면 needle false-positive.

## 6. 위험 / 방어책 (검증됨)
| 위험 | 방어책 | 검증 |
|---|---|---|
| 운영/민감 경로 capture | **denylist > allowlist** 우선 | Q2/Q3/Q4 ✅ |
| 미지정 경로 capture | **fail-closed**(unknown BLOCK) | Q8 ✅ |
| raw 노출 | id/hash만(raw 경로/원문 저장 0), source pointer 공개 미포함 | Q5 ✅ |
| 폭주 | **rate limit** | Q6 ✅ |
| 긴급 중단 | **kill switch** | Q7 ✅ |
| secret 내용 | **block-only redaction**(차단, 치환 0) | 내부 로직(SECRET_NEEDLES) |
| 자동 write | **write opt-in 없으면 staging write 0** | Q9 ✅ |
| 자동 실행 | **hook/daemon NOT_STARTED**(설치/실행 0) | Q10 ✅ |

## 7. v0.3.0-rc1 포함 가능 여부
**✅ 포함 가능** — v0.3.0-rc1 후보 = **Phase 4 reviewer/confirmed selftest + Phase 6 manual capture selftest**. 둘 다 검증 자산(read-only·write/confirmed 실행 0)이라 공개 안전.
- **포함**: Phase 4 selftest(9/9·confirmed_created=0) + Phase 6 manual capture selftest(10/10·read-only) + 결과 문서.
- **제외**: daemon/hook(종료조건 고정 후) · confirmed 실제 실행 · **Phase 5 finalize**(Neo4j/upload = v0.4+ 또는 별도 GO-OC2).
- write 기본 OFF·MCP write 미노출 유지.

## 8. 안전 경계 (계속 HOLD)
daemon 실행 · hook 설치 · 자동 staging write/apply/promote · confirmed 실제 생성 · OpenCrab upload/finalize · Neo4j start/add · 운영 store write · MCP write 노출 · RC 반영(별도 GO) · production/v09/ARMED.

## 9. 다음 단계
- **v0.3.0-rc1 묶음 반영(GO-A)**: Phase 4 + Phase 6 selftest·결과 문서 RC 복사 + clean install 검증 + README v0.3 문구 → GO-B push.
- **또는 Phase 5 정적 생성기**: neo4j/ 3종·quality/report 생성기(Neo4j 실행 0).
