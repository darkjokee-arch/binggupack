# BingguPack — save-intent D3 canary non-retention 게이트 결과 (2026-06-12)

> 설계 정본 `BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md` §5 D3 단계 실측.
> **로컬 wrangler dev 한정 — deploy 0 · live URL 호출 0 · 외부 네트워크 0 · 실 DB 0.**

## 산출물 (신규 4파일)

| 파일 | 역할 |
|---|---|
| `hosted/workers/src/save_intent.ts` | 적재 전용 worker 모듈 — IntentStore(cap 32·TTL purge·pull=drain) + shape 검증 11종 + Origin 가드. payload 로깅 0 |
| `hosted/workers/src/index.save.ts` | 로컬 dev 전용 entry |
| `hosted/workers/wrangler.save.toml` | 별도 worker name(`binggupack-save-intent-local`) — 오배포해도 라이브(`binggupack-workers-port-local`) 무영향 |
| `scripts/openbinggu_save_intent_d3_canary.py` | D3 진입 게이트 — canary 실측 16케이스, fail-closed exit |

## 게이트 결과 — **16/16 GATE=GO** (첫 실행)

- P0 dev 기동 / P1 적재 200+intent_id / P2 pull 1건 전 필드(source=hosted·created_ts·ttl_s 86400) / P3 pull 재해시=러너 `intent_hash` 일치 / P4 temp outbox `<intent_id>.json` 인계 / P5 **2차 pull 0건(store non-retention)**
- N1 오토큰 404 / N2 브라우저 Origin 403 / N3 비JSON 400 / N4 schema_ver 400 / N5 confirm 불일치 400 / N6 indices 빈 배열 400 / N7 text 캡(36,000자) 초과 400 / N8 GET 405 / N9 적재 응답 marker echo 0
- R1+R2 **canary marker 잔존 0** — dev stdout/stderr 로그 + 작업 워커 디렉토리 `.wrangler` 산출물 전체 바이너리 스캔 hits=0

## 회귀

- 러너 selftest **16/16 GO** 불변 (`openbinggu_save_intent_outbox_runner.py --selftest`)
- 라이브 코드 경로 무수정: `index.ts`·`index.real.ts`·`load_packs.ts`·`wrangler.real.toml` 미터치
- 종료 후 포트 8799 LISTEN 0·잔여 wrangler 프로세스 0

## 설계 정합 확인

- worker = 적재만(§0): DB write 0, 판정 게이트는 러너 §3 그대로 (worker는 모양 검증만)
- write 경로 분리(§4): `SAVE_PATH_TOKEN` 별도 env — read 경로(`MCP_PATH_TOKEN`)와 무관, dev에서 매 실행 랜덤 생성(평문 보관 0)
- 12지시 r2 지시 2: non-retention 을 **게이트로 실측** — 선언 아님

## 다음 (전부 별도 GO)

- D4: 4조건 게이트 검증표 작성 + 실측 통과
- D5: live 노출 — **owner 명시 GO 의무**
- RC 반영(commit/push): 신규 4파일 + 본 결과 문서 — 별도 GO
