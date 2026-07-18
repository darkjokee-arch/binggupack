# 빙구팩 hosted 신뢰 경계 (클라우드 inbox)

> 빙구팩의 **정본은 내 PC 안 로컬 장부(`ledger.sqlite`)** 입니다. 클라우드는 원본을 갖지 않습니다.
> 단 하나의 예외가 **hosted save-intent**: 다른 기기/웹에서 "이 문장 저장하고 싶다"는 의도를
> 잠깐 클라우드 inbox 에 두었다가, 내 PC 에서 **pull** 할 때 로컬로 가져오고 클라우드에선 지웁니다.
> 이 문서는 그 잠깐의 경계를 못 박습니다.

검증 한 명령: `python tests/hosted_boundary_e2e.py`

## 클라우드 inbox 에 잠깐 들어가는 것

| 항목 | 내용 |
|---|---|
| 무엇이 | save-intent 1건 = `text`(저장하려는 문장), `indices`(고른 번호), `confirm`(SAVE 문구), 메타(`created_ts`/`ttl_s`/`intent_id`) |
| 무엇이 **아닌** 것 | 전체 대화·원본 로그·장부 내용. 클라우드는 장부를 갖지 않음(로컬 SQLite write 0) |
| 어디에 | Cloudflare Worker + **Durable Object 단일 inbox** (현행 `save_intent_v2.ts`. v1 in-memory 판은 superseded) |

## 경계 5가지 (코드가 실제로 지키는 것)

### 1. TTL (보관 기간)
- 기본 **24시간**(`DEFAULT_TTL_S=86400`), 상한 **7일**(`TTL_CAP_S`). 범위 밖 ttl = `ttl_invalid` 거부.
- 클라우드: 만료 시점에 **alarm + lazy purge 2중**으로 `storage.delete` — 만료 = **삭제**(마킹 아님, 잔존 0).
- 로컬: 만료 intent 는 **저장 BLOCK**(`.expired` 마킹, 미적용).

### 2. 삭제 보장
- 클라우드 **pull = drain**: 트랜잭션 read+delete(atomic) — 가져가는 즉시 inbox 에서 소거(non-retention).
- 로컬: pull 로 **적용된 intent 만** staging 에서 제거(commit-narrow). 거부/만료분은 사람 재검토용으로 잠시 보존.

### 3. 원문 보관 범위 (최소화)
- 클라우드 Worker: **payload 로깅 0** — 전달 통로일 뿐, 본문을 로그/DB 에 남기지 않음.
- 로컬 마킹 파일(`.expired`/`.rejected`): **원문 미보관** — `text_sha`/`text_len` 으로만 대체. 평문 장기 잔존 차단.
- 마킹 파일도 **7일 후 purge**(메타까지 삭제).

### 4. pull 후 purge
- 양측 모두 ✅ — 클라우드 drain 즉시 소거 + 로컬 적용분 staging 제거. **pull 이 끝나면 클라우드에 남는 게 없음.**

### 5. 암호화 — ⚠️ 한계 (정직하게)
- **저장 시 본문 암호화는 없습니다.** 전송은 TLS(HTTPS), 무결성은 HMAC 서명(`save_common.ts`)으로 위변조를 막지만, inbox 에 잠깐 머무는 동안 본문은 **평문**입니다.
- 그래서 이렇게 보완합니다:
  - **민감정보는 애초에 후보가 안 됨** — 비밀번호·연락처·PII·secret 은 preview/캡처 단계에서 제외되어 intent 에 실리지 않습니다.
  - **TTL 이 짧고**(기본 24h) **pull 후 잔존 0** — 노출 창이 작습니다.
  - 정본은 로컬이라, 클라우드 inbox 가 비어도 데이터 손실이 없습니다.
- **hosted 는 현재 라이브 운영 중**(폰 SAVE n → PC 러너 pull → 로컬 장부 저장, 2026-06-12 라이브 실증). 본문 저장 암호화(예: 봉투 암호화)는 **미구현 한계로 존치**합니다 — TTL 24h·pull 즉시 drain·PII/secret 사전 차단으로 노출 창을 보완합니다. 봉투 암호화 추가 여부 = owner 결정 항목.

## 빠른 점검
```bash
python tests/hosted_boundary_e2e.py    # TTL/삭제/원문범위/pull-purge 로컬 검증 (GO/NO-GO)
```
이 E2E 는 doctor 게이트가 안 보던 hosted 경로를 묶은 회귀 가드입니다.
