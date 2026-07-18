# BingguPack — hosted save-intent 설계 (정본, 2026-06-11)

> **[P1-A 정합 노트]** §1 스키마의 confirm "사람 발화 증거" 라벨은 **UNTRUSTED_INTENT_ONLY**(전송된
> confirm = untrusted intent binding/형식 검증일 뿐), §3 step5 "ctx.actor auto/reader 불가"는 hosted 유래
> intent 에 대해 **SUPERSEDED_IN_PART**(trusted approval event 없으면 fail-closed=actor=reader). transport
> ≠ authority(§0) 및 intent_id rehash(무결성)는 유지. 사람 승인 = out-of-band trusted approval event
> (`docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md`). ★ hosted save-intent **runner** 봉인은 STILL-OPEN(P1-B).
> 상세 = RFC §23.

> ⚠ **[2026-07-12 저장 게이트 개정 노트]** 위 P1-A 노트의 "사람 승인 = trusted approval event" 및 P1-B 의
> hosted 묶음 exact-bound approval 커밋은 저장 게이트 개정으로 다시 대체됨 — hosted commit 의 사람 증명은
> **inbox preview + 사람의 `SAVE n` 입력**(save-n 참조 바인딩 · `CLAUDECODE` deny 가드 · isatty 미사용).
> transport ≠ authority·intent_id rehash·all-or-nothing 단일 COMMIT 은 유지. trusted approval event 는
> 비-저장 mutation 자산으로 존치. 정본: `CHANGELOG.md` [1.21.0] · `SECURITY.md`.

> DRAFT(`BINGGUPACK_HOSTED_SAVE_BUTTON_DESIGN_DRAFT.md`)를 승격·구체화한 정본.
> ⚠ **(2026-06-12 구현 상태)** 구현·라이브 완료 — D5 는 owner 명시 GO 로 진행됨(2026-06-12 라이브 실증).
> 결과: `docs/_archive/BINGGUPACK_SAVE_INTENT_V23_LIVE_E2E_RESULT.md` · 운영 정본: `hosted/workers/README.md`.
> 아래 §32 '구현(selftest)' 서술 참조. (이전 '설계만 — 코드 0 · live 노출 영구 금지' 문구는 이 노트로 대체)

## 0. 원칙 (불변)

- hosted worker = **전달 통로**. worker의 DB write 0 — 최종 write는 로컬 게이트(`save_selected`)만.
- intent의 `text`는 전달 통로일 뿐 — DB에는 로컬 게이트가 사용자가 고른 문장 전체를 저장(원문=대화 전문 저장 0).
- candidate-only · promotion 0 · confirmed 자동 생성 0 · 자동 적용 0.

### 0-1. collect broad, commit narrow (owner 확정 2026-06-13)

- **mobile/web collects** — 폰/웹은 넓게 모으기만(candidate). **PC review/confirm commits** — ledger 저장은 PC 에서 사람이 검토·확정.
- **no daemon · no autopull · no autosave** — 상주 데몬 0 · 주기적 자동 pull 0 · 백그라운드 자동 write 0.
- 2-동사 흐름(둘 다 사람이 직접 실행):
  - `binggu hosted inbox [--since Nd]` — worker 1회 회수(non-retention=drain 이라 불가피) → **로컬 staging 보존(저장 0)** → read-only 요약(80자 발췌·sha8·count·PII/secret flag·expired flag). 번호는 `--since` 와 무관하게 **전체 기준 고정**(본 번호 == pull 번호).
  - `binggu hosted pull --select 1,3 --confirm "LIVE SAVE 1,3"` — staging 의 고른 항목만 `process_outbox` 위임 commit. 미선택은 staging 잔류. confirm = `"LIVE SAVE " + ",".join(select)` 정확 일치(전량 자동 적용 차단).
- 구현: `scripts/binggu_hosted_inbox.py`(selftest 15/15) · `binggu.py hosted {inbox,pull}`(selftest 26/26). worker/게이트 무변경 — 회수 계층만 임시→영속 staging 으로 분리.

## 1. save-intent 스키마 (JSON, schema_ver=1)

| 필드 | 타입 | 규칙 |
|---|---|---|
| `schema_ver` | int | 1 고정. 불일치 = 즉시 reject |
| `intent_id` | str(16) | `sha256(text + "|" + ",".join(indices) + "|" + confirm)[:16]` — 무결성 앵커 |
| `text` | str | 대화 원문(전달 통로). 러너가 `capture_preview` 재실행에만 사용 |
| `indices` | int[] | 1-base 선택 인덱스. 비어있으면 reject |
| `confirm` | str | `"SAVE i,j"` — `"SAVE " + ",".join(indices)` 정확 일치 의무 (사람 발화 증거) |
| `created_ts` | int | epoch 초 (worker 수신 시각) |
| `ttl_s` | int | 기본 86400. `now > created_ts + ttl_s` = expired 폐기 |
| `source` | str | `"hosted"` 고정 |

**위변조 방어 2겹**: ① outbox 적재는 **write 전용 토큰**으로만 가능(read 토큰과 완전 분리 — read 토큰 유출 시 write 불가) ② 러너가 `intent_id`를 필드에서 **재해시 → 일치 의무**(불일치 = `.rejected`). text/indices/confirm 어느 하나라도 변조되면 재해시가 깨진다.

## 2. outbox — 1단계는 로컬 디렉토리

- worker KV/큐 연동 **이전에**, 로컬 디렉토리 outbox부터 시작 (D2에서 러너 단독 검증).
- 구조: outbox 디렉토리에 **파일 1건 = intent 1건**, 파일명 = `<intent_id>.json`.
- 처리 결과: 적용 성공 = 파일 소거 / 실패 = `<intent_id>.json.rejected`로 개명 보존(내부에 `reject_reason` 추가) / TTL 만료 = `.rejected` + reason=`expired`(마킹만, **미적용**).
- D3 이후 worker는 동일 스키마 intent를 적재만(POST 수신 → 검증 → 저장소 적재), 로컬 러너가 pull — 전송 계층만 바뀌고 게이트는 불변.

## 3. 로컬 러너 의무 게이트 (순서 고정 — 우회 0)

1. **schema_ver 검증** — 1 아니면 reject (`schema_mismatch`)
2. **TTL 만료 폐기** — expired 마킹·미적용 (`expired`)
3. **intent_id 재해시 일치** — 불일치 = 위변조 reject (`intent_id_mismatch`)
4. **confirm 형식 검증** — `"SAVE " + ",".join(indices)` 정확 일치 (`confirm_phrase_mismatch`)
5. **`save_selected(db, text, indices, ctx, snap_dir)` 호출** — A0 재판정·PII/secret 재스캔·기존재 skip·duplicate registry·backup/checksum rollback·confirm 재검증 **전체 게이트 그대로**. ctx.actor는 auto/reader 불가(G4_no_auto 유지) — hosted 유래임은 audit으로 구분, 게이트 완화 0
6. **적용 후**: intent 파일 소거 + audit append (`hosted_intent` action, intent 원문은 **해시만** 기록)
7. **실패 intent**: `.rejected` 보존 (사유 코드 포함) — 재시도는 사람 재승인으로만 (자동 재시도 0)

## 4. 4조건 정리표

| 조건 | 확정 설계 |
|---|---|
| **인증 상향** | read 토큰과 분리된 **write 전용 토큰**(짧은 TTL) + Origin 가드(브라우저 Origin 403, absent 허용) + 도구 단위 권한 분리. read 토큰 유출 시 write 불가 구조 |
| **전송 경로** | worker = intent **적재만**(DB write 0) → 로컬 러너 pull → §3 게이트 전체 통과 시에만 로컬 staging write. 1단계는 로컬 outbox 디렉토리(§2) |
| **audit** | 수신·거부·적용 **전 구간** audit chain (conv_save 체계 재사용 + `hosted_intent` action 신설). intent 원문은 audit에 **해시만** — 전문 미저장 |
| **rollback** | 로컬 적용분 = 기존 스냅샷/checksum 원복 그대로. outbox = TTL 폐기 + 재승인(사람)으로만 재투입. 부분쓰기 0 (staging_apply rollback 기실증) |

## 5. 단계표 D1~D5 (각 단계 **별도 GO** 의무)

| 단계 | 내용 | GO 조건 |
|---|---|---|
| D1 | 본 설계 확정 (이 문서) | owner 리뷰 GO |
| D2 | 로컬 outbox 러너 구현 + temp selftest (tempfile.mkdtemp만) | 별도 GO |
| D3 | worker save-intent 도구 — **로컬 wrangler dev 한정** 실증 | 별도 GO |
| D4 | 4조건 게이트 검증표 작성 + 실측 통과 | 별도 GO |
| D5 | **live 노출** | **owner 명시 GO** |

## 6. 명시 금지 재확인

- D5 전 live 노출 0 · live deploy/wrangler deploy 0 · 외부 네트워크 호출 0
- hosted 직접 DB write 0 (worker는 적재만)
- 자동 적용 0 — 러너 실행은 사람 또는 명시 스케줄, **1단계는 수동 실행만**
- real staging DB 접근·수정 0 · confirmed 자동 생성 0 · OpenCrab 연동 0 · marketplace/결제 0
- 토큰 회전·팀/멀티유저 권한·save UX 제품화 = 2차 (본 설계 범위 밖)

---

## 12지시 정합 (r2, 2026-06-11 — 정본: BINGGUPACK_R3_BOUNDARY_DEBATE_CONCLUSION.md)

- **worker non-retention = 검증 항목** (지시 2): D3(worker 연동) 진입 게이트에 **canary payload 실측** 의무 — 식별 가능한 합성 payload를 보내고 platform 로그/trace/analytics 어디에도 잔존하지 않음을 확인해야 D3 통과. 선언 조항이 아니라 게이트다. **live 구현 아님 — v1.x live 전 필수 게이트.**
- **outbox 최소 보안안 채택** (지시 3): v1.0 기준 — 마킹(.rejected/.expired) 파일은 **payload 원문 미보관**(text_sha/text_len 대체), 활성 intent 만 TTL 창 내 한시 보관. 암호화는 v1.x 후보.
- **이중 TTL** (지시 4): intent TTL(만료=.expired, 자동 적용 금지 BLOCK) + 마킹 파일 TTL(MARKER_TTL_S=7일, 러너 시작 시 만료 삭제 — marked_ts 부재도 fail-closed 삭제).
- **경로 가드** (지시 2): outbox UNC/네트워크 경로 거부·symlink/junction 거부·intent 파일은 outbox 직속 비링크만(intent_path_rejected).
- 구현 정본: `scripts/openbinggu_save_intent_outbox_runner.py` (selftest 16/16 — 가드 3종 회귀 포함).
