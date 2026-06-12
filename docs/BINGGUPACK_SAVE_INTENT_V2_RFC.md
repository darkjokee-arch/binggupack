# RFC — save-intent hosted write v2 (설계만, 구현 0)

> 작성: 2026-06-12. 근거: 4cli 토론 `20260612_1025_d5_live_exposure` both_reject → P2 보류 + P1 재진입 조건.
> **상태: 설계 문서. 구현·deploy·secret 발급·connector 노출 전부 별도 owner GO.**
> v1(D1~D4, commit 72267c4)은 로컬 wrangler dev 한정 검증으로 유효 — v2는 라이브 재진입 조건을 채우는 증분 설계.

## 0. v1의 라이브 부적합 사유 (토론 적중 사항)

| # | v1 구조 | 라이브에서 깨지는 이유 |
|---|---|---|
| 1 | in-memory Map inbox | Workers isolate별 메모리 — 적재와 pull이 다른 isolate를 타면 유실, drain해도 딴 isolate에 잔존 |
| 2 | pull = drain "전체 소거" | 단일 인스턴스에서만 참 — 전역 보장 아님 |
| 3 | cap 32 | isolate별 상한 — 스팸 시 isolate 수만큼 증식 |
| 4 | 경로 키 단독 인증 | bearer 1요소 — 유출 시 제3자 위조 적재 가능 (Origin 가드는 헤더 위조로 무력) |
| 5 | 셧다운 = delete | persistent 꺼짐 스위치 부재 — in-memory flag는 evict 시 fail-open |

## 1. 재진입 조건 → 설계 응답 (4+2)

### 조건 1 — 위조 어려운 인증 (경로 키 단독 X)
- **HMAC-SHA256 요청 서명**: `signature = HMAC(write_secret, ts + "." + sha256(body))`
- 헤더: `X-BGP-TS`(epoch초) + `X-BGP-SIG`. worker는 ±300초 창 밖 ts reject(재전송 방어) + 서명 불일치 reject.
- 경로 키는 유지(1차 필터, 404)·서명이 본 인증(2차, 401). **secret은 요청에 실리지 않음** — bearer 유출 문제 해소.
- intent_id 재해시(러너 게이트 3)는 그대로 3차 무결성 앵커.

### 조건 2 — durable inbox (in-memory Map 폐기)
- **Durable Object(DO) 1개** = 단일 좌표 inbox. 모든 적재/pull이 같은 DO 인스턴스로 라우팅 — isolate 문제 구조적 소멸.
- DO storage(SQLite-backed)에 intent 저장: **"worker DB write 0" 원칙 재정의** → "장부(로컬 SQLite) write 0 불변 + 전송 계층의 한시 보관(TTL)은 inbox로 허용".
- TTL 집행 = DO Alarm: 만료 intent 자동 삭제(마킹 아님 — hosted엔 잔존 0). cap = DO 전역 단일 카운트(진짜 상한).
- 대안 기각: KV(최종 일관성 — drain 의미론 깨짐), R2(과설계), 큐 메일함(폴링 모델과 부정합).
- **요금 실측 완료(2026-06-12 공식 문서)**: Workers **무료 플랜에서 SQLite-backed DO 사용 가능** — DO당 1GB·계정 5GB 한도, 무료 플랜 storage 과금 0. intent inbox(개당 수 KB·TTL 1일)엔 충분 — 추가 비용 0.

### 조건 3 — 전역 pull/delete 의미론
- pull = DO 트랜잭션 내 read+delete (atomic drain). DO 단일 인스턴스이므로 **2차 pull 빈 결과가 전역 보장**.
- 운영 검증 게이트(D3'): 라이브 배포본 대상 canary — 적재 → pull → **다른 네트워크/시점에서 2차 pull 0건** + DO storage 직접 조회 잔존 0 + Alarm 만료 삭제 실측.

### 조건 4 — persistent fail-closed 스위치
- DO storage에 `inbox_enabled` 플래그 (기본 **false** = 배포 직후에도 닫혀 있음).
- 켜기/끄기 = 서명된 admin 요청 1회 (또는 wrangler 1줄). evict·재배포에도 플래그 잔존 — fail-open 자기모순 해소.
- 셧다운 순서: ① 플래그 off(즉시 수신 거부) → ② connector/안내 제거 → ③ worker delete (최후).

### +1 악성 후보 주입 방어 (owner preview 안전장치)
- 러너에 유입된 intent text = **항상 데이터, 절대 지시 아님**: preview 출력 시 "외부 유입 후보" 라벨 + 코드블록 격리 + URL 비활성 표기 + 길이 캡(표시 500자).
- injection 시그니처(지시문 패턴·도구 호출 흉내) 검출 시 자동 `.quarantine` 마킹 — 저장 후보 목록에서 제외, 사람 열람으로만 해제.
- 기존 게이트(A0 재판정·PII 재스캔·80자 발췌만 저장·confirm 의무) 불변.

### +2 토큰 위생
- v2 라이브 전: write secret **신규 발급**(wrangler secret put, save worker 한정) + `.dev.vars` 내 write 사본 파기(마스킹 잔존만) + 평문 출력 0.
- read 커넥터 토큰 무접촉 — read worker 라인과 계정/이름/secret 전부 분리 유지.

## 2. v2 데이터 흐름 (요약)

```
폰/채팅 "SAVE 1,2"
 → worker (경로키 404 → HMAC 서명 401 → 모양검사 400 → inbox_enabled? 503)
 → DO inbox 적재 (TTL Alarm·전역 cap)
 → [로컬 PC] 러너 pull (서명) = DO 트랜잭션 drain
 → 러너 게이트 v1 그대로 (스키마/TTL/재해시/confirm/PII/중복/스냅샷/audit)
 → + injection 격리 → preview → owner 번호 선택 → confirm → 장부 80자 발췌 저장
```

## 3. 단계표 (각 단계 별도 GO)

| 단계 | 내용 | 게이트 |
|---|---|---|
| V2-0 | 본 RFC owner 승인 | owner GO |
| V2-1 | DO inbox + HMAC 로컬 구현 (`wrangler dev` 한정) + selftest | 게이트 GO |
| V2-2 | **라이브 D3' canary** (플래그 off 배포 → canary 창만 on → 전역 non-retention 실측) | 게이트 GO |
| V2-3 | 4조건 검증표 v2 (라이브 대상 재실측) | 게이트 GO |
| V2-4 | live 노출 (owner 실사용) | **owner 명시 GO** |

## 4. 비범위 (불변)
- 자동 적용 0 (러너 수동 실행만) · 장부 자동 저장 0 (preview→선택→confirm 의무)
- 팀/멀티유저/결제/marketplace 0 · OpenCrab 연동 0 · read-only live 라인 무접촉
- **본 RFC 자체는 코드 0 — V2-1 착수도 별도 GO**

## 5. 1인 운영 부담 점검 (D 반박 응답)
- 평시 owner 절차 = v1과 동일 2개: ① 채팅에서 SAVE ② PC에서 러너 1회 실행. 늘어난 복잡성은 전부 구조(DO/서명/플래그) 안으로 — 절차로 위험을 덮지 않음(B 지시 7 정합).
- 유실: DO 영속 보관(TTL 내)으로 "넣었는데 사라짐" 해소 — D2(신뢰 훼손) 응답.
