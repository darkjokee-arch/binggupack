# RFC — save-intent V2-A: MCP 커넥터 어댑터 (폰 연결 마지막 조각)

> **[P1-A 정합 노트]** §1 save_intent description 의 "'SAVE n,m' 발화 시 호출"은 승인 신호가 아니라
> **UNTRUSTED_INTENT_ONLY**(모델 행동 힌트). §0 "적재 강도 ≠ 저장 안전"은 KEEP 이나, "최종 저장 권한은
> 로컬 러너 게이트(…confirm)에만 있음" 중 **confirm=권한 절만 SUPERSEDED_IN_PART**(confirm 은 형식 검증일 뿐
> 사람 승인 아님). 사람 승인 = trusted approval event(`docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md`).
> transport/HMAC/inbox/injection 격리는 유지(직교). 상세 = RFC §23.

> 작성 2026-06-12. owner A안 GO + 자물쇠 "읽기 동급" 선택.
> 배경: V2-2/V2-3에서 금고(DO inbox)는 검증됐으나 **폰 claude.ai → 금고 호출선이 부재**.
>   원인 = save worker가 커스텀 HMAC API라 MCP 커넥터가 등록 불가(initialize 400).
> **(2026-06-12 구현 상태) 구현됨: `hosted/workers/src/save_intent_mcp.ts` (운영 config `wrangler.save_mcp.prod.toml` · `/mcp2` 라우트 라이브 · `save_intent` 도구 tools/list 노출). read 라인(62팩) 무접촉(여전히 정확).**

## 0. 핵심 결정 (토론 C 반박 대응 포함)

- **이중 인증 도메인** — 인증을 용도별로 분리:
  - **적재(폰, MCP)**: `POST /mcp2/<경로키>` JSON-RPC + Origin 가드. claude.ai 커넥터는 HMAC 헤더를 못 보내므로 **경로키(추측 불가 24자)+Origin = read 라인 동급**. ← owner 선택
  - **인출·관리(PC 러너)**: `POST /save2/<경로키>/{pull,admin/*}` + **HMAC 서명 유지**(PC는 서명 생성 가능). 강한 인증 그대로.
- **약한 적재의 정당성(토론 C "write 인증 약함" 반박 해소)**: 적재는 휘발 inbox에 넣을 뿐. 최종 저장 권한은 **로컬 러너 게이트**(재해시·PII·중복·preview·번호선택·confirm)에만 있음. 가짜 적재는 러너에서 전건 차단(V2-3 C2-3 실증). worker 적재 강도 ≠ 저장 안전.
- **read 라인 무접촉**: 별도 worker·별도 커넥터. 폰에 커넥터 2개(읽기 62팩 + 저장).

## 1. MCP 인터페이스 (폰 적재용)

- `initialize` → protocolVersion·serverInfo (read worker 형식 정합)
- `tools/list` → 도구 1개: **`save_intent`**
  - input: `{ text: string, indices: number[], confirm: string }`
  - description에 명시: "사용자가 명시적으로 'SAVE n,m'을 발화했을 때만 호출. 자동/추론 호출 금지." (capture preview 명령형 패턴 정합)
- `tools/call save_intent`:
  1. inbox enabled? 아니면 `inbox_disabled` (fail-closed 기본 off)
  2. shape 검증(v2 동일): schema·text 캡·indices·confirm 형식("SAVE "+indices)
  3. **worker가 intent_id 계산** = `sha256(text + "|" + indices.join(",") + "|" + confirm)[:16]` (러너 `intent_hash`와 바이트 동일 — 재해시 일치 의무)
  4. inbox DO put(전역 cap·TTL) → 반환 `{ intent_id, ttl_s }` (text echo 0)
- pull/admin은 MCP에 **미노출** — 폰이 인출·플래그를 못 만짐.

## 2. 인출·관리 (PC 러너, 기존 유지)

- `POST /save2/<경로키>/pull` (HMAC) → DO atomic drain → 러너 outbox
- `POST /save2/<경로키>/admin/{enable,disable}` (HMAC) → fail-closed 플래그
- 러너 게이트 = v1 그대로 (스키마·TTL·재해시·confirm·PII·중복·스냅샷·audit) → preview → 번호선택 → confirm → 로컬 장부 문장 전체 저장.

## 3. 보안 점검표 (토론 4 반박 매핑)

| 토론 반박 | V2-A 대응 |
|---|---|
| C "write 인증 약함, 무인증 주입→preview까지 도달" | 적재=경로키+Origin(read 동급). injection은 러너 preview에서 **외부 유입 라벨+격리**(RFC §1 +1) — 명령 아닌 텍스트로만 |
| C "isolate별 cap/잔존" | V2-2에서 DO 단일 inbox 전역 의미론 라이브 실증 — 해소됨 |
| C "fail-open flag" | DO storage 플래그(evict 생존)·기본 off — V2-1/2/3 실증 |
| D "1인 운영 피로" | 폰 절차 = SAVE 발화 1회 / PC = 러너 1회. 늘지 않음 |

## 4. 단계 (각 별도 GO)

| 단계 | 내용 | 게이트 |
|---|---|---|
| A-0 | 본 설계 owner 승인 | ✅ (A안+읽기동급 선택) |
| A-1 | MCP 어댑터 로컬 구현 + selftest(initialize/tools/list/tools/call·Origin·경로키·적재→DO→drain·재해시 일치) | 게이트 GO |
| A-2 | 라이브 배포(별도 save-mcp worker, inbox 기본 off) + canary | **owner GO** |
| A-3 | 폰 커넥터 등록 + owner 실 SAVE 1건 → 러너 → 실 장부 | **owner GO + 폰 발화** |

## 5. 불변
- read 라인(62팩) 무접촉 · 자동 적용 0 · 장부 자동 저장 0(preview→번호→confirm) · 적재 강도와 무관하게 최종 권한=러너 게이트 · pull/admin은 폰 미노출 · Workers Logs off.
