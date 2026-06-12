# BingguPack — save-intent V2-2 라이브 D3' canary 결과 (2026-06-12)

> owner 승인 문구 "V2-2 배포 고" 하에 집행. 설계 정본 `BINGGUPACK_SAVE_INTENT_V2_RFC.md` §3 V2-2.
> **합성 canary만 — 실 사용자 데이터 0. read-only live 라인 무접촉. inbox 최종 = 잠김(fail-closed).**

## 결과 — 15/15 GATE=GO (라이브 실측)

worker: `binggupack-save-intent-v2.<account>.workers.dev` (read 라인과 name 완전 분리)

- P 전파 확인 / L1 기본 비활성 503 / L2 enable 200 / L3 무서명 401 / L4 ts 창 밖 401 / L5 변조 401 / L6 오토큰 404 / L7 Origin 403 / L8 canary put 200 / L9 echo 0
- **L10 라이브 pull 1건 + 재해시 일치 (전역 라우팅)** / **L11 2차 pull 0건 (전역 atomic drain)** / **L12 5초 후 3차 0건** / **L13 TTL 만료 라이브 소각 0건** / L14 disable 후 503 재잠금

→ 토론(20260612_1025) 핵심 지적 "DO 전역 의미론이 라이브에서 보장되는가"가 **실 Cloudflare에서 실증**됨: 단일 DO inbox로 colo 경유 pull도 1건 정확·2·3차 0건·만료 자동 소각.

## 결함 → 수정 체인 ⭐

1. **1차 통합 실행 BLOCK(4/17)** — 원인은 코드 아님. http 클라이언트가 **Cloudflare 1010(python 기본 UA 차단)** 에 막혀 거의 모든 요청이 403(1010) → 기대 상태 불일치. 박제 [feedback_cloudflare_1010_custom_ua] 정확 일치. **custom User-Agent 고정**(`binggupack-canary/1.0`)으로 해소 → 재검증 15/15 GO. 교훈: 라이브 검증 스크립트는 UA 고정 의무, 1010=CF 레벨이지 서버 결함 아님.
2. deploy/secret 단계는 1차에서 정상 완료(returncode 0·secret list 2종) — 검증부만 분리 재실행(`..._canary_verify.py`)이 효율적.

## 운영 상태 (현재)

- save-v2 worker **배포됨 + inbox 잠김(503 inbox_disabled)** — 서명 있어도 적재 거부 (V2-4 owner GO 전 기본 off 유지)
- secret 2종(SAVE_PATH_TOKEN·SAVE_SIGN_SECRET) CF에만 존재 / 로컬 사본 `workers_port/.dev.vars.save_v2`(git 비추적, V2-3 러너용) / 평문 출력 0
- read-only live(62팩) GET 405 정상 — 무영향
- Workers Logs/observability 미활성 (payload 기록 0)

## 비상 셧다운 (RFC §1 조건4 순서)

1. `admin/disable`(서명) — 즉시 수신 거부 (현재 상태)
2. (connector 미노출 — 해당 없음)
3. `npx wrangler delete --config wrangler.save_v2.prod.toml` — worker 제거

## 단계 상태

- V2-0 RFC ✅ / V2-1 로컬 16/16 ✅ / **V2-2 라이브 canary 15/15 ✅**
- 다음: V2-3 라이브 4조건 재실측표 / **V2-4 live 노출 = owner 명시 GO**(러너 pull 실사용 — inbox enable 포함)
