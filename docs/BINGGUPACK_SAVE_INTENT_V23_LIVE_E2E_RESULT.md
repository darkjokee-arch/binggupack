# BingguPack — save-intent V2-3 라이브 결합 4조건 E2E 결과 (2026-06-12)

> owner "승인" 하에 집행. 설계 정본 `BINGGUPACK_SAVE_INTENT_V2_RFC.md` §3 V2-3.
> D4(로컬 worker) 4조건을 **라이브 worker + 로컬 러너 + temp DB** 결합으로 재실측.
> **합성만 — 실 사용자 데이터 0. 로컬 장부 미접촉(temp DB). read 라인 무접촉. inbox 최종 잠김.**

## 결과 — 15/15 GATE=GO

worker = live `binggupack-save-intent-v2.<account>.workers.dev` (enable→실측→disable)
러너·DB = 로컬 temp (`openbinggu_save_intent_outbox_runner.process_outbox` + `open_g3` temp sqlite)

| 조건 | 케이스 | 결과 |
|---|---|---|
| 1 인증 | 오토큰 404·Origin 403·무서명 401·재전송 창 밖 401 | ✅ |
| 2 전송 | worker 적재 후 로컬 DB 노드 0 / live pull→outbox→러너 applied·노드·파일소거 / 변조=러너 reject | ✅ |
| 3 audit | hosted_intent ALLOW row / 로컬 DB 원문 전문 잔존 0 / chain INTACT | ✅ |
| 4 rollback | live TTL 소각 0건+러너 무적용 / 자동 재시도 0 / 스냅샷 / 종료 시 inbox 503 재잠금 | ✅ |

→ D4의 4조건이 **로컬 시뮬이 아닌 실 Cloudflare worker를 거쳐도 동일 성립**. 전달(live)·판정(로컬 러너)·저장(temp 발췌)·audit 전 구간 결합 실증.

## 단계 상태

- V2-0 RFC ✅ / V2-1 로컬 16/16 ✅ / V2-2 라이브 canary 15/15 ✅ / **V2-3 라이브 결합 E2E 15/15 ✅**
- **V2-4 live 노출 = 마지막 단계**: inbox enable 유지 + **owner 폰에서 실제 SAVE 발화 → 로컬 러너 pull/적용**. 실 사용자(owner 본인) 데이터가 처음 들어가는 단계 — owner 직접 인터랙션 필수.

## V2-4 실사용 절차 (owner 인터랙션)

1. (claude/PC) inbox enable (서명 admin 요청)
2. (owner 폰) BingguPack 커넥터에 save worker 등록 후 채팅에서 후보 preview → `SAVE 1,2` 발화
3. (claude/PC) 러너 실행 = live pull → 로컬 게이트 → preview → owner 번호 선택 → confirm → **로컬 장부(real)** 80자 발췌 저장
4. 종료 시 inbox 정책 결정 (상시 enable vs 사용 후 disable)

> 주의: V2-4는 temp가 아닌 **실 로컬 장부 write** + owner 실 대화 — 별도 명시 GO + owner 폰 발화 의무.
